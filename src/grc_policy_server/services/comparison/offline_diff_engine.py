"""Offline comparison engine — zero external service dependencies.

Subclasses RealDiffEngine and overrides only the LLM-dependent methods
(summary generation, markdown diff summaries, follow-up questions).
All deterministic comparison logic (ClauseMatcher, ChangeRecords,
SeverityClassifier, TableDiffEngine) runs unchanged from the parent.

Usage:
    engine = OfflineDiffEngine(canonical_store=store, trace_store=trace)
    result = await engine.compare(doc1, doc2, audit_mode=True)
"""
from __future__ import annotations

import logging
import re
from typing import List

from grc_policy_server.models.schemas import KeyDifference
from grc_policy_server.services.comparison.change_records import ChangeRecord
from grc_policy_server.services.comparison.clause_matcher import MatchThresholds
from grc_policy_server.services.comparison.comparison_trace import ComparisonTraceStore
from grc_policy_server.services.comparison.real_diff_engine import RealDiffEngine
from grc_policy_server.services.documents.canonical_store import CanonicalDocumentStore
from grc_policy_server.services.llm.noop_llm import NoOpLLM

logger = logging.getLogger(__name__)

_NOOP_LLM = NoOpLLM()


class OfflineDiffEngine(RealDiffEngine):
    """Comparison engine that works with only PostgreSQL/file storage.

    External dependencies NOT required:
        - Weaviate (set to None → ClauseMatcher uses pure text matching)
        - Ollama / vLLM (replaced by NoOpLLM + deterministic overrides)
        - Neo4j (set to None → citation fallback to canonical store)
        - Redis / Celery (callers run compare() directly via asyncio.run())
    """

    def __init__(
        self,
        *,
        canonical_store: CanonicalDocumentStore,
        trace_store: ComparisonTraceStore | None = None,
        thresholds: MatchThresholds = MatchThresholds(),
        topk: int = 5,
        max_diffs: int = 40,
    ) -> None:
        super().__init__(
            qdrant=None,
            neo4j=None,
            llm=_NOOP_LLM,
            canonical_store=canonical_store,
            trace_store=trace_store,
            thresholds=thresholds,
            topk=topk,
            max_diffs=max_diffs,
        )

    # ------------------------------------------------------------------
    # Overrides — replace LLM-dependent methods with deterministic logic
    # ------------------------------------------------------------------

    async def _enrich_nodes_with_semantics(
        self,
        nodes: list[dict],
        force_re_extract: bool = False,
        language: str = "",
    ) -> list[dict]:
        """Run parent rule-based enrichment then apply multilingual numeric normalisation.

        Normalises decimal commas and thousands separators in `clean_text` so
        that the ClauseMatcher's text-ratio comparison is not confused by locale
        differences when comparing DE or FR documents against each other or
        against an EN reference.
        """
        enriched = await super()._enrich_nodes_with_semantics(
            nodes, force_re_extract=force_re_extract, language=language
        )
        lang = (language or "").split("-")[0].lower()
        if lang in ("de", "fr"):
            normalise = _make_numeric_normaliser(lang)
            for node in enriched:
                clean = node.get("clean_text")
                if clean:
                    node["clean_text"] = normalise(clean)
        return enriched

    async def _populate_markdown_diff_summaries(
        self,
        diffs: List[KeyDifference],
        change_records: list[ChangeRecord] | None = None,
        *,
        language: str = "",
    ) -> None:
        """Skip LLM markdown generation in offline mode.

        markdownDiffSummary fields remain None; the front-end renders raw
        doc1Content / doc2Content instead.
        """
        return

    async def _summary_from_change_records(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        change_records: list[ChangeRecord],
        key_differences: list[KeyDifference],
        llm_payload: dict,
        language: str,
    ) -> str:
        return _deterministic_summary(doc1_name, doc2_name, key_differences)

    async def _follow_ups(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        diffs: List[KeyDifference],
        language: str,
    ) -> List[str]:
        return _deterministic_followups(diffs)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


_DE_THOUSANDS = re.compile(r"(\d)\.(\d{3})(?=[^\d]|$)")
# Matches ASCII space, NBSP (U+00A0), and thin space (U+202F) as FR thousands sep.
_FR_THOUSANDS = re.compile(r"(\d)[ \xa0 ](\d{3})(?=[^\d]|$)")
_DECIMAL_COMMA = re.compile(r"(\d),(\d)")


def _make_numeric_normaliser(lang: str):
    """Return a function that normalises locale numeric punctuation for *lang*."""

    def _de(text: str) -> str:
        result = text
        while True:
            new = _DE_THOUSANDS.sub(r"\1\2", result)
            if new == result:
                break
            result = new
        return _DECIMAL_COMMA.sub(r"\1.\2", result)

    def _fr(text: str) -> str:
        result = text
        while True:
            new = _FR_THOUSANDS.sub(r"\1\2", result)
            if new == result:
                break
            result = new
        return _DECIMAL_COMMA.sub(r"\1.\2", result)

    return _de if lang == "de" else _fr


def _deterministic_summary(
    doc1_name: str,
    doc2_name: str,
    key_differences: list[KeyDifference],
) -> str:
    if not key_differences:
        return f"No material differences detected between {doc1_name} and {doc2_name}."

    total = len(key_differences)
    severity_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    change_type_counts: dict[str, int] = {"ADDED": 0, "REMOVED": 0, "MODIFIED": 0}
    sections: list[str] = []

    for diff in key_differences:
        sev = (getattr(diff, "changeSeverity", None) or "low").lower()
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        ct = getattr(diff, "changeType", "MODIFIED") or "MODIFIED"
        change_type_counts[ct] = change_type_counts.get(ct, 0) + 1
        sec = (getattr(diff, "section", None) or "").strip()
        if sec and sec not in sections:
            sections.append(sec)

    # Build severity breakdown string.
    sev_parts: list[str] = []
    if severity_counts["high"]:
        sev_parts.append(f"{severity_counts['high']} high")
    if severity_counts["medium"]:
        sev_parts.append(f"{severity_counts['medium']} medium")
    if severity_counts["low"]:
        sev_parts.append(f"{severity_counts['low']} low")
    sev_str = ", ".join(sev_parts) + " severity" if sev_parts else "unknown severity"

    # Build change-type breakdown.
    ct_parts: list[str] = []
    if change_type_counts["ADDED"]:
        ct_parts.append(f"{change_type_counts['ADDED']} added")
    if change_type_counts["REMOVED"]:
        ct_parts.append(f"{change_type_counts['REMOVED']} removed")
    if change_type_counts["MODIFIED"]:
        ct_parts.append(f"{change_type_counts['MODIFIED']} modified")
    ct_str = f" ({', '.join(ct_parts)})" if ct_parts else ""

    # Top affected sections.
    top_sections = sections[:3]
    section_str = (
        f" Affected sections include: {', '.join(top_sections)}."
        if top_sections
        else ""
    )

    note = (
        " [Offline mode — deterministic summary; LLM narrative unavailable.]"
        if severity_counts["high"] == 0
        else " Manual review required for high-severity findings."
    )

    return (
        f"Comparison of {doc1_name!r} vs {doc2_name!r}: "
        f"{total} difference(s) detected{ct_str}. "
        f"Severity breakdown: {sev_str}.{section_str}{note}"
    )


def _deterministic_followups(diffs: list[KeyDifference]) -> list[str]:
    if not diffs:
        return [
            "Are there any material compliance requirement changes between these versions?",
            "Which sections require immediate policy updates?",
        ]

    questions: list[str] = []
    high_sev = [d for d in diffs if getattr(d, "changeSeverity", "") == "high"]
    for diff in (high_sev or diffs)[:4]:
        sec = getattr(diff, "section", "") or "the affected section"
        ct = getattr(diff, "changeType", "MODIFIED") or "MODIFIED"
        if ct == "ADDED":
            questions.append(
                f"What compliance obligations does the new content in '{sec}' introduce?"
            )
        elif ct == "REMOVED":
            questions.append(
                f"What is the compliance risk from removing the content in '{sec}'?"
            )
        else:
            questions.append(
                f"What is the compliance impact of the change detected in '{sec}'?"
            )

    return questions[:4]
