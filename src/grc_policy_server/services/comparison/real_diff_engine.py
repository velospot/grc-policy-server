from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import combinations
from typing import List, Optional

from grc_policy_server.core.logging import logging
from grc_policy_server.models.schemas import (
    ActionItem,
    ChangeDetail,
    ComparisonAccuracyMetrics,
    ComparisonResult,
    Document,
    DocumentReference,
    KeyDifference,
    SectionAccuracyMetrics,
)
from grc_policy_server.services.comparison.change_records import (
    ChangeRecord,
    detect_emc_entity_type,
    detect_numeric_changes,
    detect_requirement_verb_change,
    detect_test_procedure_change,
    detect_test_setup_change,
    is_cosmetic_text_change,
    is_formatting_only_change,
    is_reference_number_only_change,
    is_structural_label_change,
)
from grc_policy_server.services.comparison.clause_matcher import (
    ClauseMatch,
    ClauseMatcher,
    ClauseMatchingResult,
    MatchThresholds,
)
from grc_policy_server.services.comparison.comparison_trace import ComparisonTraceStore
from grc_policy_server.services.comparison.diff_postprocessor import (
    filter_key_differences,
    random_diff_subset,
)
from grc_policy_server.services.comparison.policy_semantics import (
    ClauseMeaning,
    clean_policy_text,
    compare_clause_meaning,
    ends_with_terminal_punctuation,
    extract_clause_meaning,
    is_docling_orphan_fragment,
    is_non_semantic_content,
    starts_with_lowercase,
)
from grc_policy_server.services.comparison.severity_classifier import (
    AuditDisposition,
    ClassificationContext,
    SeverityClassifier,
)
from grc_policy_server.services.documents.canonical_models import (
    TEXT_COMPARISON_NODE_TYPES,
)
from grc_policy_server.services.documents.canonical_store import CanonicalDocumentStore
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.llm.base import BaseLLM
from grc_policy_server.services.vector.weaviate_client import WeaviateClient
from grc_policy_server.utils.hashing import pure_text_hash as _pure_text_hash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PII masking — applied to content fields in key_difference output
# ---------------------------------------------------------------------------
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b", re.IGNORECASE), "[EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),  # SSN before PHONE (more specific)
    (re.compile(r"\b(?:\+?\d[\d\s\-().]{7,}\d)\b"), "[PHONE]"),
]


_SKIP_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"preface|foreword|acknowledgements?|table\s+of\s+contents|list\s+of\s+(?:figures|tables)"
    r"|revision\s+history|document\s+history|change\s+log|changelog|change\s+record"
    r"|copyright|intellectual\s+property|legal\s+notice|disclaimer|signature"
    r"|blank\s+page|intentionally\s+left\s+blank"
    r"|vorwort|danksagung|inhaltsverzeichnis|abbildungsverzeichnis|tabellenverzeichnis"
    r"|revisionshistorie?|änderungsverzeichnis|urheberrecht|impressum"
    r"|avant-propos|remerciements|table\s+des\s+matières|liste\s+des\s+figures"
    r"|historique\s+des\s+révisions|mentions\s+légales"
    r")\b",
    re.IGNORECASE,
)


def _mask_pii(text: str | None) -> str | None:
    if not text:
        return text
    for pat, placeholder in _PII_PATTERNS:
        text = pat.sub(placeholder, text)
    return text


# ---------------------------------------------------------------------------
# Normative strength helpers (Phase E2)
# ---------------------------------------------------------------------------


def _extract_normative_strength(text: str) -> int:
    """Return the numeric strength of the strongest obligation term found in *text*.

    Uses NormalizedFactExtractor for fact-level precision, falling back to -1
    (unknown) if no normative term is found or the extractor is unavailable.
    Strength scale from OBLIGATION_STRENGTH in policy_semantics.py:
      may=0, should=1, recommended=2, required=3, must=4, shall=5
    """
    try:
        from grc_policy_server.services.comparison.policy_semantics import (
            OBLIGATION_STRENGTH,
        )
        from grc_policy_server.services.ingestion.ontology.emc_ontology import (
            NormalizedFactExtractor,
        )

        extractor = NormalizedFactExtractor()
        facts = extractor.extract_from_cell(text or "", column_name="")
        strengths = [
            OBLIGATION_STRENGTH.get((f.value or "").lower(), -1)
            for f in facts
            if getattr(f, "fact_type", "") == "normative_term"
        ]
        return max(strengths) if strengths else -1
    except Exception:
        return -1


def _evidence_from_node(node: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build an evidence record from a canonical node dict for citation attachment.

    Returns a dict with node_id, page, section, heading_path, and node_type
    so auditors can trace every ChangeRecord back to its source PDF location.
    """
    if not node:
        return None
    return {
        "node_id": str(node.get("node_id") or ""),
        "node_type": str(node.get("node_type") or ""),
        "page": node.get("page_from") or node.get("page") or None,
        "section": str(
            (node.get("section_path") or node.get("heading_path") or ["Unknown"])[-1]
        ),
        "heading_path": node.get("heading_path") or node.get("section_path") or [],
    }


def _normative_strength_change(
    left_text: str, right_text: str
) -> dict[str, str] | None:
    """Compare normative strength between old and new text using fact extraction.

    Returns a dict with "old_strength", "new_strength", "direction" when a
    meaningful strength change is detected, or None when unchanged / indeterminate.
    Complements text-level `detect_requirement_verb_change` for obligation terms
    that appear inside tables rather than free-form clause prose.
    """
    old_s = _extract_normative_strength(left_text)
    new_s = _extract_normative_strength(right_text)
    if old_s < 0 or new_s < 0 or old_s == new_s:
        return None
    direction = "strengthened" if new_s > old_s else "weakened"
    return {
        "old_strength": str(old_s),
        "new_strength": str(new_s),
        "direction": direction,
    }


# Caption row: a table row 0 whose single spanning cell is "Table N …" / "Figure N …"
_CAPTION_ROW_RE = re.compile(r"^(?:table|tbl\.?|figure|fig\.?)\s*\d", re.IGNORECASE)
# Reference-only tokens: table/figure/section numbers inline in text
_REF_TOKEN_RE = re.compile(
    r"\b(?:table|tbl\.?|figure|fig\.?|section|sec\.?|clause|annex|appendix)\s*[\d][\d.\-]*\b",
    re.IGNORECASE,
)


def severity_from_distance(distance: Optional[float], change_type: str) -> str:
    """Severity is the inverse of matchScore (distance = 1 − matchScore).

    matchScore ≈ 1.0  →  distance ≈ 0.0  →  "low"   (barely changed)
    matchScore ≈ 0.5  →  distance ≈ 0.5  →  "medium"
    matchScore ≈ 0.0  →  distance ≈ 1.0  →  "high"  (completely different)

    ADDED / REMOVED nodes have no counterpart so they are always "high".
    """
    if change_type in ("ADDED", "REMOVED"):
        return "high"
    if distance is None:
        return "high"
    # distance > 0.60  →  matchScore < 0.40
    if distance > 0.60:
        return "high"
    # distance > 0.35  →  matchScore < 0.65
    if distance > 0.35:
        return "medium"
    # distance ≤ 0.35  →  matchScore ≥ 0.65
    return "low"


def _reconstruct_canonical_table(node: dict) -> "Any | None":
    """Reconstruct a minimal CanonicalTable from a comparison-record node dict.

    Returns None if the node has no cell data (non-table or missing structure).
    """
    try:
        from grc_policy_server.services.documents.canonical_table_model import (
            CanonicalTable,
            TableCell,
            TableColumn,
            TableRow,
        )

        meta = node.get("metadata") or {}
        ts = meta.get("table_structure") or {}
        cells_raw = ts.get("cells") or node.get("table_cells") or []
        headers_raw = ts.get("headers") or []
        if not cells_raw:
            return None

        headers = [str(h) for h in headers_raw]
        cols = [
            TableColumn(index=i, name=h, normalized=h.lower())
            for i, h in enumerate(headers)
        ]

        rows_by_idx: dict[int, list[dict]] = {}
        for c in cells_raw:
            r = int(c.get("row", 0))
            rows_by_idx.setdefault(r, []).append(c)

        canonical_rows = []
        for r_idx in sorted(rows_by_idx):
            row_cells = [
                TableCell(
                    row=r_idx,
                    col=int(c.get("col", 0)),
                    text=str(c.get("text", "") or ""),
                    is_header=bool(c.get("is_header", False)),
                )
                for c in rows_by_idx[r_idx]
            ]
            canonical_rows.append(TableRow(row_number=r_idx, cells=row_cells))

        caption = str(meta.get("caption") or node.get("heading_text") or "")
        return CanonicalTable(
            table_uid=str(node.get("node_id") or ""),
            caption_original=caption,
            caption_normalized=caption.lower(),
            section_path=list(node.get("section_path") or []),
            pages=[int(node.get("page_from") or 1)],
            columns=cols,
            rows=canonical_rows,
            metadata={
                "language": str(node.get("detected_language") or ""),
                "emc_test_type": str(meta.get("emc_test_type") or ""),
            },
        )
    except Exception:
        logger.debug("_reconstruct_canonical_table failed", exc_info=True)
        return None


@dataclass
class RealDiffEngine:
    weaviate: WeaviateClient | None
    neo4j: Neo4jClient | None
    llm: BaseLLM
    canonical_store: CanonicalDocumentStore | None = None
    trace_store: ComparisonTraceStore | None = None
    thresholds: MatchThresholds = MatchThresholds()
    topk: int = 5
    max_diffs: int = 40
    severity_classifier: SeverityClassifier = field(default_factory=SeverityClassifier)

    def _weaviate_search_fn(self):
        """Return a search callable that silently falls back on any Weaviate error."""
        if self.weaviate is None:
            return None
        _weaviate = self.weaviate

        def _search(*args, **kwargs):
            try:
                return _weaviate.search_section_in_document(*args, **kwargs)
            except Exception:
                logger.warning(
                    "Weaviate search failed during compare — continuing without vector search",
                    exc_info=True,
                )
                return []

        return _search

    async def compare(
        self,
        doc1: Document,
        doc2: Document,
        force_re_extract: bool = False,
        audit_mode: bool = True,
        save_to_db: bool = False,
        testing_department: str = "",
    ) -> ComparisonResult:
        left_nodes = self._load_comparison_nodes(doc1.id)
        right_nodes = self._load_comparison_nodes(doc2.id)
        left_nodes = self._stitch_page_fragments(left_nodes)
        right_nodes = self._stitch_page_fragments(right_nodes)

        # Detect language from first document's text for better LLM accuracy
        language = await self._detect_document_language(left_nodes)
        logger.info("detected document language=%s", language or "unknown")

        left_nodes = await self._enrich_nodes_with_semantics(
            left_nodes, force_re_extract=force_re_extract, language=language
        )
        right_nodes = await self._enrich_nodes_with_semantics(
            right_nodes, force_re_extract=force_re_extract, language=language
        )
        left_nodes = self._filter_non_compliance_nodes(left_nodes)
        right_nodes = self._filter_non_compliance_nodes(right_nodes)
        logger.info(
            "compare left_nodes=%s right_nodes=%s", len(left_nodes), len(right_nodes)
        )

        matcher = ClauseMatcher(
            search_fn=self._weaviate_search_fn(),
            thresholds=self.thresholds,
            topk=self.topk,
            language=language,
        )
        matching = matcher.match(
            left_nodes=left_nodes,
            right_nodes=right_nodes,
            target_document_id=doc2.id,
        )
        matching = self._detect_moves(
            matching,
            matcher=matcher,
        )
        matching, grouped_alignments = self._detect_split_merge_alignments(
            matching,
            matcher=matcher,
        )

        change_records = self._build_change_records(
            matching, language=language, testing_department=testing_department
        )
        for alignment_type, left_group, right_group, distance in grouped_alignments:
            change_records.append(
                self._change_record_for_pair(
                    change_type="MODIFIED",
                    alignment_type=alignment_type,
                    left_nodes=left_group,
                    right_nodes=right_group,
                    distance=distance,
                    meaning_change="modified",
                    testing_department=testing_department,
                )
            )
        diffs = [self._key_difference_from_record(record) for record in change_records]

        # Filter non-semantic diffs and their corresponding change_records together
        # to avoid pairing mismatches after filtering
        import re

        from grc_policy_server.services.comparison.diff_postprocessor import (
            canonicalize_text_content,
        )

        _REFERENCE_SECTION_RE = re.compile(
            r"\b(legende|symbole?|abkürzung(?:en)?|definitionen?|begriffe?|inhalt"
            r"|glossar|annex|anhang|abbreviation|legend|symbol|glossary|definition)\b",
            re.IGNORECASE,
        )
        _TABLE_CAPTION_NUM_RE = re.compile(r"\bTabell?e\s+\d+", re.IGNORECASE)

        def _should_filter_diff(diff: KeyDifference) -> bool:
            """Return True if diff should be filtered out (non-semantic)."""
            section = str(diff.section or "")
            if not section and diff.doc1Reference:
                section = str(diff.doc1Reference.section or "")
            if not section and diff.doc2Reference:
                section = str(diff.doc2Reference.section or "")

            # Drop reference-section diffs unless they have a numbered caption
            if _REFERENCE_SECTION_RE.search(
                section
            ) and not _TABLE_CAPTION_NUM_RE.search(section):
                return True

            # Don't filter diffs that have structural changes
            has_structural_change = any(
                change.location == "structure"
                or "split" in change.text.lower()
                or "merge" in change.text.lower()
                for change in (diff.changes or [])
            )
            if has_structural_change:
                return False

            # Drop MODIFIED diffs with identical content in the SAME section (pure cosmetic change)
            # But keep diffs where section or location changed (semantic difference)
            if (
                diff.changeType == "MODIFIED"
                and diff.doc1Reference
                and diff.doc2Reference
            ):
                # Check if section/location is the same
                old_section = str(diff.doc1Reference.section or "").strip()
                new_section = str(diff.doc2Reference.section or "").strip()
                if (
                    old_section == new_section and old_section
                ):  # Same location, check content
                    old_text_src = diff.doc1Reference.sourceText
                    new_text_src = diff.doc2Reference.sourceText
                    # Only filter if we have identical source text (purely cosmetic)
                    if old_text_src and new_text_src:
                        old_text = canonicalize_text_content(str(old_text_src))
                        new_text = canonicalize_text_content(str(new_text_src))
                        if old_text and new_text and old_text == new_text:
                            return True

            return False

        filtered_diffs = []
        filtered_records = []
        for diff, record in zip(diffs, change_records):
            if not _should_filter_diff(diff):
                filtered_diffs.append(diff)
                filtered_records.append(record)
        diffs = filtered_diffs
        change_records = filtered_records

        paired = sorted(
            zip(diffs, change_records),
            key=lambda pair: (
                ("High", "Medium", "Low").index(pair[0].impact)
                if pair[0].impact in ("High", "Medium", "Low")
                else 2
            ),
        )
        diffs, change_records = (list(x) for x in zip(*paired)) if paired else ([], [])

        llm_payload = self._change_records_llm_payload(
            doc1=doc1,
            doc2=doc2,
            change_records=change_records,
            language=language,
        )

        await self._populate_markdown_diff_summaries(
            diffs, change_records, language=language
        )

        summary = await self._summary_from_change_records(
            doc1_name=doc1.name,
            doc2_name=doc2.name,
            change_records=change_records,
            key_differences=diffs,
            llm_payload=llm_payload,
            language=language,
        )
        follow_up_questions = await self._follow_ups(
            doc1_name=doc1.name,
            doc2_name=doc2.name,
            diffs=diffs,
            language=language,
        )
        accuracy_metrics = self._compute_accuracy_metrics(matching.matches)

        self._save_compare_trace(
            doc1=doc1,
            doc2=doc2,
            left_nodes=left_nodes,
            right_nodes=right_nodes,
            matching=matching,
            grouped_alignments=grouped_alignments,
            change_records=change_records,
            diffs=diffs,
            language=language,
            llm_payload=llm_payload,
            summary=summary,
            follow_up_questions=follow_up_questions,
        )

        hidden_diffs_count = 0
        if not audit_mode:
            visible_diffs = [d for d in diffs if d.changeSeverity != "low"]
            hidden_diffs_count = len(diffs) - len(visible_diffs)
            diffs = visible_diffs

        require_human_review = any(d.requiresHumanReview for d in diffs)

        result = ComparisonResult(
            summary=summary,
            keyDifferences=diffs,
            actionPlan=self._action_plan(diffs),
            followUpQuestions=follow_up_questions,
            accuracyMetrics=accuracy_metrics,
            comparisonMode="auditor_grade" if audit_mode else "simple",
            requireHumanReview=require_human_review,
            hiddenDiffsCount=hidden_diffs_count,
        )

        if save_to_db:
            self._try_save_comparison_to_postgres(doc1.id, doc2.id, result, audit_mode)

        return result

    async def compare_records_only(
        self,
        doc1: Document,
        doc2: Document,
        *,
        force_re_extract: bool = False,
        testing_department: str = "",
    ) -> tuple[
        list[KeyDifference], str, str, str, list[dict], ComparisonAccuracyMetrics
    ]:
        """Run the comparison pipeline without LLM markdown generation.

        Returns (key_differences, doc1_name, doc2_name, language,
                 no_change_coverage, accuracy_metrics).
        Used by the streaming path to generate markdown per-diff inline.
        """
        left_nodes = self._load_comparison_nodes(doc1.id)
        right_nodes = self._load_comparison_nodes(doc2.id)
        left_nodes = self._stitch_page_fragments(left_nodes)
        right_nodes = self._stitch_page_fragments(right_nodes)

        language = await self._detect_document_language(left_nodes)

        left_nodes = await self._enrich_nodes_with_semantics(
            left_nodes, force_re_extract=force_re_extract, language=language
        )
        right_nodes = await self._enrich_nodes_with_semantics(
            right_nodes, force_re_extract=force_re_extract, language=language
        )
        left_nodes = self._filter_non_compliance_nodes(left_nodes)
        right_nodes = self._filter_non_compliance_nodes(right_nodes)

        matcher = ClauseMatcher(
            search_fn=self._weaviate_search_fn(),
            thresholds=self.thresholds,
            topk=self.topk,
            language=language,
        )
        matching = matcher.match(
            left_nodes=left_nodes,
            right_nodes=right_nodes,
            target_document_id=doc2.id,
        )
        matching = self._detect_moves(matching, matcher=matcher)
        matching, grouped_alignments = self._detect_split_merge_alignments(
            matching, matcher=matcher
        )

        change_records = self._build_change_records(
            matching, language=language, testing_department=testing_department
        )
        for alignment_type, left_group, right_group, distance in grouped_alignments:
            change_records.append(
                self._change_record_for_pair(
                    change_type="MODIFIED",
                    alignment_type=alignment_type,
                    left_nodes=left_group,
                    right_nodes=right_group,
                    distance=distance,
                    meaning_change="modified",
                    testing_department=testing_department,
                )
            )
        diffs = [self._key_difference_from_record(record) for record in change_records]

        import re as _re

        from grc_policy_server.services.comparison.diff_postprocessor import (
            canonicalize_text_content,
        )

        _REF_SECTION_RE = _re.compile(
            r"\b(legende|symbole?|abkürzung(?:en)?|definitionen?|begriffe?|inhalt"
            r"|glossar|annex|anhang|abbreviation|legend|symbol|glossary|definition)\b",
            _re.IGNORECASE,
        )
        _TBL_CAP_RE = _re.compile(r"\bTabell?e\s+\d+", _re.IGNORECASE)

        def _should_filter(diff: KeyDifference) -> bool:
            section = str(diff.section or "")
            if not section and diff.doc1Reference:
                section = str(diff.doc1Reference.section or "")
            if not section and diff.doc2Reference:
                section = str(diff.doc2Reference.section or "")
            if _REF_SECTION_RE.search(section) and not _TBL_CAP_RE.search(section):
                return True
            has_structural = any(
                change.location == "structure"
                or "split" in change.text.lower()
                or "merge" in change.text.lower()
                for change in (diff.changes or [])
            )
            if has_structural:
                return False
            if (
                diff.changeType == "MODIFIED"
                and diff.doc1Reference
                and diff.doc2Reference
            ):
                old_sec = str(diff.doc1Reference.section or "").strip()
                new_sec = str(diff.doc2Reference.section or "").strip()
                if old_sec == new_sec and old_sec:
                    old_t = diff.doc1Reference.sourceText
                    new_t = diff.doc2Reference.sourceText
                    if old_t and new_t:
                        if canonicalize_text_content(
                            str(old_t)
                        ) == canonicalize_text_content(str(new_t)):
                            return True
            return False

        filtered = [
            (d, r) for d, r in zip(diffs, change_records) if not _should_filter(d)
        ]
        diffs, change_records = (
            (list(x) for x in zip(*filtered)) if filtered else ([], [])
        )

        paired = sorted(
            zip(diffs, change_records),
            key=lambda pair: (
                ("High", "Medium", "Low").index(pair[0].impact)
                if pair[0].impact in ("High", "Medium", "Low")
                else 2
            ),
        )
        diffs = [d for d, _ in paired] if paired else []
        final_change_records = [r for _, r in paired] if paired else []

        no_change_coverage = self._compute_no_change_coverage(
            matching, final_change_records
        )
        accuracy_metrics = self._compute_accuracy_metrics(matching.matches)
        return (
            diffs,
            doc1.name,
            doc2.name,
            language,
            no_change_coverage,
            accuracy_metrics,
        )

    def _load_comparison_nodes(self, document_id: str) -> list[dict]:
        if self.canonical_store is not None:
            canonical_nodes = self.canonical_store.load_comparison_nodes(document_id)
            if canonical_nodes:
                logger.info(
                    "loaded canonical comparison nodes document_id=%s nodes=%s",
                    document_id,
                    len(canonical_nodes),
                )
                return canonical_nodes
            raise ValueError(
                "Canonical comparison nodes were not found for document "
                f"{document_id}. Re-ingest the document before comparing."
            )

        if self.weaviate is None:
            raise ValueError(
                f"No data source available for document {document_id}. "
                "Ensure canonical_store is configured."
            )
        logger.warning(
            "canonical comparison nodes unavailable; falling back to Weaviate "
            "document_id=%s",
            document_id,
        )
        try:
            return self.weaviate.fetch_chunks_by_document(document_id)
        except Exception as exc:
            raise ValueError(
                f"No data source available for document {document_id}. "
                "Canonical store returned no nodes and Weaviate is unreachable."
            ) from exc

    def _detect_moves(
        self,
        matching: ClauseMatchingResult,
        *,
        matcher: ClauseMatcher,
    ) -> ClauseMatchingResult:
        if not matching.removed or not matching.added:
            return matching

        candidate_edges: list[tuple[float, float, str, str, dict, dict]] = []
        for left_node in matching.removed:
            left_id = str(left_node.get("chunk_id") or "")
            if not left_id or self._is_non_semantic_node(left_node):
                continue
            for right_node in matching.added:
                right_id = str(right_node.get("chunk_id") or "")
                if not right_id or self._is_non_semantic_node(right_node):
                    continue
                if not self._node_types_compatible(left_node, right_node):
                    continue

                stable_id = str(left_node.get("stable_id") or "")
                same_stable_id = stable_id and stable_id == str(
                    right_node.get("stable_id") or ""
                )
                actual_score = matcher._clause_score(  # noqa: SLF001
                    left_node,
                    right_node,
                )
                ranking_score = 1.0 if same_stable_id else actual_score
                if ranking_score < max(0.72, self.thresholds.min_clause_score):
                    continue
                candidate_edges.append(
                    (
                        ranking_score,
                        actual_score,
                        left_id,
                        right_id,
                        left_node,
                        right_node,
                    )
                )

        candidate_edges.sort(key=lambda item: item[0], reverse=True)
        moved_matches: list[ClauseMatch] = []
        moved_left: set[str] = set()
        moved_right: set[str] = set()
        for _, score, left_id, right_id, left_node, right_node in candidate_edges:
            if left_id in moved_left or right_id in moved_right:
                continue
            moved_matches.append(
                ClauseMatch(
                    distance=1.0 - score,
                    matched_by="moved",
                    left=left_node,
                    right=right_node,
                )
            )
            moved_left.add(left_id)
            moved_right.add(right_id)

        if not moved_matches:
            return matching

        return ClauseMatchingResult(
            matches=[*matching.matches, *moved_matches],
            removed=[
                node
                for node in matching.removed
                if str(node.get("chunk_id") or "") not in moved_left
            ],
            added=[
                node
                for node in matching.added
                if str(node.get("chunk_id") or "") not in moved_right
            ],
            section_matches=matching.section_matches,
        )

    def _detect_split_merge_alignments(
        self,
        matching: ClauseMatchingResult,
        *,
        matcher: ClauseMatcher,
    ) -> tuple[ClauseMatchingResult, list[tuple[str, list[dict], list[dict], float]]]:
        alignments: list[tuple[str, list[dict], list[dict], float]] = []
        consumed_left: set[str] = set()
        consumed_right: set[str] = set()

        for left_node in matching.removed:
            left_id = str(left_node.get("chunk_id") or "")
            if not left_id or left_id in consumed_left:
                continue
            candidates = [
                node
                for node in matching.added
                if str(node.get("chunk_id") or "") not in consumed_right
                and self._node_types_compatible(left_node, node)
            ]
            split = self._best_group_alignment(
                source_node=left_node,
                candidates=candidates,
                matcher=matcher,
                source_on_left=True,
            )
            if split is None:
                continue
            right_group, distance = split
            alignments.append(("split", [left_node], right_group, distance))
            consumed_left.add(left_id)
            consumed_right.update(
                str(node.get("chunk_id") or "") for node in right_group
            )

        for right_node in matching.added:
            right_id = str(right_node.get("chunk_id") or "")
            if not right_id or right_id in consumed_right:
                continue
            candidates = [
                node
                for node in matching.removed
                if str(node.get("chunk_id") or "") not in consumed_left
                and self._node_types_compatible(node, right_node)
            ]
            merge = self._best_group_alignment(
                source_node=right_node,
                candidates=candidates,
                matcher=matcher,
                source_on_left=False,
            )
            if merge is None:
                continue
            left_group, distance = merge
            alignments.append(("merged", left_group, [right_node], distance))
            consumed_right.add(right_id)
            consumed_left.update(str(node.get("chunk_id") or "") for node in left_group)

        if not alignments:
            return matching, []

        return (
            ClauseMatchingResult(
                matches=matching.matches,
                removed=[
                    node
                    for node in matching.removed
                    if str(node.get("chunk_id") or "") not in consumed_left
                ],
                added=[
                    node
                    for node in matching.added
                    if str(node.get("chunk_id") or "") not in consumed_right
                ],
                section_matches=matching.section_matches,
            ),
            alignments,
        )

    def _best_group_alignment(
        self,
        *,
        source_node: dict,
        candidates: list[dict],
        matcher: ClauseMatcher,
        source_on_left: bool,
    ) -> tuple[list[dict], float] | None:
        if len(candidates) < 2:
            return None
        scored = sorted(
            [
                (
                    (
                        matcher._clause_score(source_node, candidate)  # noqa: SLF001
                        if source_on_left
                        else matcher._clause_score(candidate, source_node)  # noqa: SLF001
                    ),
                    candidate,
                )
                for candidate in candidates
            ],
            key=lambda item: item[0],
            reverse=True,
        )
        top_candidates = [candidate for _, candidate in scored[:6]]
        best_score = 0.0
        best_group: list[dict] = []
        for group_size in (2, 3, 4):
            for group in combinations(top_candidates, group_size):
                combined = self._combined_node(list(group))
                score = (
                    matcher._clause_score(source_node, combined)  # noqa: SLF001
                    if source_on_left
                    else matcher._clause_score(combined, source_node)  # noqa: SLF001
                )
                if score > best_score:
                    best_score = score
                    best_group = list(group)
        if best_score < 0.55:
            return None
        return best_group, 1.0 - best_score

    def _stitch_page_fragments(self, nodes: list[dict]) -> list[dict]:
        """Merge adjacent same-section, same-type chunks split at page boundaries.

        Operates at comparison time so existing ingested docs benefit without
        re-ingestion. Uses _combined_node() to concatenate text/clean_text fields.
        """
        if not nodes:
            return nodes
        ordered = sorted(
            nodes,
            key=lambda n: (
                str(n.get("section_path") or ""),
                int(n.get("page_number") or 0),
                int(n.get("chunk_index") or 0),
            ),
        )
        result: list[dict] = []
        i = 0
        stitched_count = 0
        while i < len(ordered):
            node = ordered[i]
            if i + 1 < len(ordered) and self._is_page_continuation(
                node, ordered[i + 1]
            ):
                first_stable_id = node.get("stable_id")
                merged = self._combined_node([node, ordered[i + 1]])
                merged["stable_id"] = first_stable_id
                j = i + 2
                while j < len(ordered) and self._is_page_continuation(
                    merged, ordered[j]
                ):
                    merged = self._combined_node([merged, ordered[j]])
                    merged["stable_id"] = first_stable_id
                    j += 1
                stitched_count += j - i
                result.append(merged)
                i = j
            else:
                result.append(node)
                i += 1
        if stitched_count:
            logger.info(
                "stitch_page_fragments: merged %d fragment(s) → %d logical node(s)",
                stitched_count,
                len(result),
            )
        return result

    def _is_page_continuation(self, prev: dict, curr: dict) -> bool:
        """Return True when curr is a page-boundary continuation of prev."""
        if prev.get("node_type") != curr.get("node_type"):
            return False
        prev_titles = list(prev.get("section_titles") or [])
        curr_titles = list(curr.get("section_titles") or [])
        if prev_titles and curr_titles:
            if prev_titles != curr_titles:
                return False
        else:
            if str(prev.get("section_path") or "") != str(
                curr.get("section_path") or ""
            ):
                return False
        prev_page = int(prev.get("page_number") or 0)
        curr_page = int(curr.get("page_number") or 0)
        if curr_page != prev_page + 1:
            return False
        node_type = str(prev.get("node_type") or "")
        if node_type == "table":
            # Allow up to 1-column discrepancy to handle Docling merged-cell artifacts
            # where continuation pages may report N±1 columns.  Larger differences
            # indicate structurally distinct tables and block stitching.
            prev_cols = int(prev.get("table_num_cols") or 0)
            curr_cols = int(curr.get("table_num_cols") or 0)
            if prev_cols > 0 and curr_cols > 0 and abs(prev_cols - curr_cols) > 1:
                return False
            # A continuation fragment has NO header cells at row 0 (the header is on the
            # previous page). An independent new table always starts with is_header=True at row 0.
            curr_cells = curr.get("table_cells") or []
            row0_cells = [c for c in curr_cells if int(c.get("row") or 0) == 0]
            if row0_cells:
                return not any(c.get("is_header") for c in row0_cells)
            # No cell data → fall back to schema_signature equality as a weaker signal
            sig = str(prev.get("table_schema_signature") or "")
            return bool(sig) and sig == str(curr.get("table_schema_signature") or "")
        if node_type in {"clause", "paragraph"}:
            prev_text = str(prev.get("text") or "").rstrip()
            curr_text = str(curr.get("text") or "").lstrip()
            if not prev_text or not curr_text:
                return False
            prev_meta = prev.get("metadata") or {}
            curr_meta = curr.get("metadata") or {}
            if prev_meta.get("is_list") or curr_meta.get("is_list"):
                return True
            return not ends_with_terminal_punctuation(
                prev_text
            ) and starts_with_lowercase(curr_text)
        return False

    def _filter_non_compliance_nodes(self, nodes: list[dict]) -> list[dict]:
        """Remove nodes whose top-level heading is a non-compliance section
        (preface, ToC, revision history, copyright, etc.).
        Filtering happens before matching to avoid noise diffs.
        """
        result = []
        skip_prefix: str | None = None
        for node in nodes:
            path: list = node.get("section_titles") or node.get("heading_path") or []
            top_heading = str(path[0]).strip() if path else ""
            path_str = " / ".join(str(p) for p in path).lower()
            if top_heading and _SKIP_HEADING_RE.match(top_heading):
                skip_prefix = top_heading.lower()
                continue
            if skip_prefix and path_str.startswith(skip_prefix):
                continue
            else:
                skip_prefix = None
            result.append(node)
        return result

    def _build_change_records(
        self,
        matching: ClauseMatchingResult,
        *,
        language: str,
        testing_department: str = "",
    ) -> list[ChangeRecord]:
        records: list[ChangeRecord] = []
        for match in matching.matches:
            if self._is_non_semantic_node(match.left) and self._is_non_semantic_node(
                match.right
            ):
                continue
            alignment_type = self._alignment_type(match)
            meaning_change = self._meaning_change(match.left, match.right, language)
            cosmetic_change = is_cosmetic_text_change(
                self._source_text(match.left),
                self._source_text(match.right),
            )
            if (
                alignment_type != "moved"
                and match.distance <= self.thresholds.unchanged_distance
                and meaning_change == "unchanged"
                and not cosmetic_change
            ):
                continue
            # Cosmetic changes (trailing punctuation, casing, etc.) pass through here and
            # are classified as LOW by the severity engine below.  They are intentionally
            # not filtered so auditors can see them.
            # Skip diff when pure-text hashes match (identical alphanumeric content).
            # Guarded by meaning_change == "unchanged" because pure_text_hash strips
            # punctuation — without the guard, "3.5 V" ≡ "35 V" could be suppressed.
            if alignment_type != "moved" and meaning_change == "unchanged":
                left_node = match.left or {}
                right_node = match.right or {}
                left_hash = (
                    left_node.get("pure_text_hash")
                    or left_node.get("metadata", {}).get("pure_text_hash")
                    or _pure_text_hash(
                        left_node.get("text") or left_node.get("clean_text") or ""
                    )
                )
                right_hash = (
                    right_node.get("pure_text_hash")
                    or right_node.get("metadata", {}).get("pure_text_hash")
                    or _pure_text_hash(
                        right_node.get("text") or right_node.get("clean_text") or ""
                    )
                )
                if left_hash and right_hash and left_hash == right_hash:
                    continue
                # For table nodes: also deduplicate by structural fingerprint when cell text
                # has minor whitespace/formatting differences that break the text hash.
                if left_node.get("node_type") == "table" == right_node.get("node_type"):
                    lsig = left_node.get("table_schema_signature", "")
                    rsig = right_node.get("table_schema_signature", "")
                    lfp = left_node.get("table_row_fingerprints") or []
                    rfp = right_node.get("table_row_fingerprints") or []
                    if lsig and rsig and lsig == rsig and lfp and lfp == rfp:
                        continue
            records.append(
                self._change_record_for_pair(
                    change_type="MODIFIED",
                    alignment_type=alignment_type,
                    left_nodes=[match.left],
                    right_nodes=[match.right],
                    distance=match.distance,
                    meaning_change=meaning_change,
                    testing_department=testing_department,
                )
            )

        for left_node in matching.removed:
            if self._is_non_semantic_node(left_node):
                continue
            records.append(
                self._change_record_for_pair(
                    change_type="REMOVED",
                    alignment_type="removed",
                    left_nodes=[left_node],
                    right_nodes=[],
                    distance=None,
                    meaning_change="removed",
                    testing_department=testing_department,
                )
            )

        for right_node in matching.added:
            if self._is_non_semantic_node(right_node):
                continue
            records.append(
                self._change_record_for_pair(
                    change_type="ADDED",
                    alignment_type="added",
                    left_nodes=[],
                    right_nodes=[right_node],
                    distance=None,
                    meaning_change="added",
                    testing_department=testing_department,
                )
            )
        return records

    def _change_record_for_pair(
        self,
        *,
        change_type: str,
        alignment_type: str,
        left_nodes: list[dict],
        right_nodes: list[dict],
        distance: float | None,
        meaning_change: str,
        testing_department: str = "",
    ) -> ChangeRecord:
        left = (
            self._combined_node(left_nodes)
            if len(left_nodes) > 1
            else (left_nodes[0] if left_nodes else None)
        )
        right = (
            self._combined_node(right_nodes)
            if len(right_nodes) > 1
            else (right_nodes[0] if right_nodes else None)
        )
        node_type = str((left or right or {}).get("node_type") or "paragraph")
        left_ref = self._citation_from_neo4j_or_fallback(left) if left else None
        right_ref = self._citation_from_neo4j_or_fallback(right) if right else None
        doc1_content = self._display_content(left) if left else None
        doc2_content = self._display_content(right) if right else None
        changes = self._record_changes(
            change_type=change_type,
            alignment_type=alignment_type,
            left=left,
            right=right,
            node_type=node_type,
            doc1_content=doc1_content,
            doc2_content=doc2_content,
        )
        left_src = self._source_text(left)
        right_src = self._source_text(right)
        numeric_changes = detect_numeric_changes(left_src, right_src)
        requirement_verb_change = detect_requirement_verb_change(left_src, right_src)
        # Supplement text-level verb detection with fact-level normative strength check
        if requirement_verb_change is None:
            requirement_verb_change = _normative_strength_change(left_src, right_src)
        cosmetic_change = is_cosmetic_text_change(left_src, right_src)
        ref_num_only = bool(numeric_changes) and is_reference_number_only_change(
            left_src, right_src
        )
        formatting_only = is_formatting_only_change(left_src, right_src)
        structural_label = is_structural_label_change(left_src, right_src)
        ontology_entity_type = detect_emc_entity_type(
            left_text=left_src,
            right_text=right_src,
            left_node=left,
            right_node=right,
        )
        test_procedure_change = detect_test_procedure_change(left_src, right_src)
        test_setup_change = detect_test_setup_change(left_src, right_src)
        table_changes = [
            change.model_dump(mode="json") for change in changes if node_type == "table"
        ]

        # KG entity-graph diff — wires the semantic KG comparison into the main pipeline
        if node_type == "table" and left is not None and right is not None:
            try:
                from grc_policy_server.services.comparison.table_diff_engine import (
                    _diff_entity_graphs,
                    _extract_table_entity_graph,
                )

                language = str(
                    left.get("detected_language")
                    or right.get("detected_language")
                    or ""
                )
                old_table = _reconstruct_canonical_table(left)
                new_table = _reconstruct_canonical_table(right)
                if old_table and new_table:
                    old_graph, tt_old = _extract_table_entity_graph(
                        old_table,
                        language=language,
                        testing_department=testing_department,
                    )
                    new_graph, tt_new = _extract_table_entity_graph(
                        new_table,
                        language=language,
                        testing_department=testing_department,
                    )
                    from grc_policy_server.services.ingestion.ontology.emc_ontology import (
                        EMCTestType,
                    )

                    resolved_tt = tt_old if tt_old != EMCTestType.UNKNOWN else tt_new
                    if any(old_graph) or any(new_graph):
                        for egc in _diff_entity_graphs(
                            old_graph, new_graph, resolved_tt
                        ):
                            table_changes.append(
                                {
                                    "type": "entity_graph_change",
                                    "entity_type": egc.get("entity_type", ""),
                                    "old_value": egc.get("old_value", ""),
                                    "new_value": egc.get("new_value", ""),
                                    "change_type": egc.get("change_type", ""),
                                    "semantic_description": egc.get(
                                        "semantic_description", ""
                                    ),
                                }
                            )
            except Exception:
                logger.debug(
                    "entity graph diff failed in change record pair", exc_info=True
                )

        classification = self.severity_classifier.classify(
            ClassificationContext(
                change_type=change_type,  # type: ignore[arg-type]
                alignment_type=alignment_type,
                node_type=node_type,
                distance=distance,
                meaning_change=meaning_change,
                numeric_changes=numeric_changes,
                requirement_verb_change=requirement_verb_change,
                table_changes=table_changes,
                cosmetic_change=cosmetic_change,
                reference_number_only_change=ref_num_only,
                formatting_only_change=formatting_only,
                structural_label_change=structural_label,
                ontology_entity_type=ontology_entity_type,
                test_procedure_change=test_procedure_change,
                test_setup_change=test_setup_change,
                testing_department=testing_department,
            )
        )
        significance = classification.severity
        impact = classification.impact
        severity = classification.severity
        reasons = classification.reasons
        severity_confidence = classification.severity_confidence
        requires_human_review = (
            classification.audit_disposition == AuditDisposition.REQUIRES_HUMAN_REVIEW
            or (severity == "high" and severity_confidence < 0.85)
            or (severity == "medium" and severity_confidence < 0.70)
        )
        section = (
            (left_ref.section if left_ref else None)
            or (right_ref.section if right_ref else None)
            or str((left or right or {}).get("section_path") or "Unknown Section")
        )
        confidence = 0.0 if distance is None else max(0.0, min(1.0, 1.0 - distance))
        if change_type in {"ADDED", "REMOVED"}:
            confidence = 1.0
        v1_evidence = [
            e for n in left_nodes if (e := _evidence_from_node(n)) is not None
        ]
        v2_evidence = [
            e for n in right_nodes if (e := _evidence_from_node(n)) is not None
        ]
        return ChangeRecord(
            change_id=self._change_id(change_type, left_nodes, right_nodes),
            change_type=change_type,  # type: ignore[arg-type]
            alignment_type=alignment_type,
            left_nodes=left_nodes,
            right_nodes=right_nodes,
            distance=distance,
            confidence=round(confidence, 4),
            node_type=node_type,
            section=section,
            doc1_content=doc1_content,
            doc2_content=doc2_content,
            doc1_reference=left_ref,
            doc2_reference=right_ref,
            changes=changes,
            meaning_change=meaning_change,
            numeric_changes=numeric_changes,
            requirement_verb_change=requirement_verb_change,
            table_changes=table_changes,
            significance=significance,
            impact=impact,
            severity=severity,
            requires_human_review=requires_human_review,
            significance_reasons=reasons,
            v1_evidence=v1_evidence,
            v2_evidence=v2_evidence,
        )

    def _record_changes(
        self,
        *,
        change_type: str,
        alignment_type: str,
        left: dict | None,
        right: dict | None,
        node_type: str,
        doc1_content: str | None,
        doc2_content: str | None,
    ) -> list[ChangeDetail]:
        if change_type == "ADDED":
            return [
                ChangeDetail(
                    type="added",
                    text=str(self._source_text(right) or doc2_content or ""),
                )
            ]
        if change_type == "REMOVED":
            return [
                ChangeDetail(
                    type="removed",
                    text=str(self._source_text(left) or doc1_content or ""),
                )
            ]

        if alignment_type in {"split", "merged"}:
            label = (
                "Node split into multiple nodes"
                if alignment_type == "split"
                else "Multiple nodes merged into one node"
            )
            return [
                ChangeDetail(
                    type="modified",
                    text=label,
                    oldValue=str(self._source_text(left) or doc1_content or ""),
                    newValue=str(self._source_text(right) or doc2_content or ""),
                    location="structure",
                )
            ]

        changes = self._extract_changes(left or {}, right or {}, node_type)
        if alignment_type == "moved" and left and right:
            old_section = str(left.get("section_path") or "Unknown Section")
            new_section = str(right.get("section_path") or "Unknown Section")
            if old_section != new_section:
                changes.insert(
                    0,
                    ChangeDetail(
                        type="modified",
                        text="Node moved between sections",
                        oldValue=old_section,
                        newValue=new_section,
                        location="section",
                    ),
                )
        return changes

    def _key_difference_from_record(self, record: ChangeRecord) -> KeyDifference:
        return KeyDifference(
            changeType=record.change_type,
            section=record.section,
            doc1Content=_mask_pii(record.doc1_content),
            doc2Content=_mask_pii(record.doc2_content),
            impact=record.impact,
            changeSeverity=record.severity,
            doc1Reference=record.doc1_reference,
            doc2Reference=record.doc2_reference,
            nodeType=record.node_type,
            changes=record.changes,
            requiresHumanReview=record.requires_human_review,
        )

    def _alignment_type(self, match: ClauseMatch) -> str:
        if match.matched_by == "moved":
            return "moved"
        left_section = str(match.left.get("section_path") or "")
        right_section = str(match.right.get("section_path") or "")
        if left_section and right_section and left_section != right_section:
            return "moved"
        return match.matched_by

    def _display_content(self, node: dict | None) -> str:
        if not node:
            return ""
        if node.get("node_type") == "table":
            return self._format_table_content(node)
        return self._short(str(node.get("text") or ""))

    def _source_text(self, node: dict | None) -> str:
        if not node:
            return ""
        return self._reference_source_text(node)

    def _comparison_text(self, node: dict) -> str:
        return str(
            node.get("comparison_text")
            or node.get("canonical_text")
            or node.get("clean_text")
            or clean_policy_text(str(node.get("text") or ""))
        ).strip()

    def _combined_node(self, nodes: list[dict]) -> dict:
        if not nodes:
            return {}
        ordered = sorted(nodes, key=self._node_sort_key)
        first = dict(ordered[0])
        texts = [str(node.get("text") or "").strip() for node in ordered]
        clean_texts = [
            self._comparison_text(node)
            for node in ordered
            if self._comparison_text(node)
        ]
        first["chunk_id"] = ",".join(
            str(node.get("chunk_id") or "") for node in ordered
        )
        first["node_id"] = first["chunk_id"]
        first["text"] = "\n\n".join(text for text in texts if text)
        first["clean_text"] = " ".join(clean_texts)
        first["canonical_text"] = first["clean_text"]
        first["comparison_text"] = first["clean_text"]
        first["node_type"] = (
            str(first.get("node_type") or "paragraph")
            if len({str(node.get("node_type") or "") for node in ordered}) == 1
            else "paragraph"
        )
        first["combined_node_ids"] = [
            str(node.get("chunk_id") or "") for node in ordered
        ]
        return first

    @staticmethod
    def _node_sort_key(node: dict) -> tuple[int, int]:
        page = node.get("page_number")
        if page is None:
            page = node.get("page")
        order = node.get("order_index")
        if order is None:
            order = node.get("chunk_index")
        return (int(page or 0), int(order or 0))

    def _node_types_compatible(self, left: dict, right: dict) -> bool:
        left_type = str(left.get("node_type") or "paragraph")
        right_type = str(right.get("node_type") or "paragraph")
        if left_type == right_type:
            return True
        return (
            left_type in TEXT_COMPARISON_NODE_TYPES
            and right_type in TEXT_COMPARISON_NODE_TYPES
        )

    @staticmethod
    def _change_id(
        change_type: str,
        left_nodes: list[dict],
        right_nodes: list[dict],
    ) -> str:
        left_ids = ",".join(str(node.get("chunk_id") or "") for node in left_nodes)
        right_ids = ",".join(str(node.get("chunk_id") or "") for node in right_nodes)
        return f"{change_type}:{left_ids}:{right_ids}"

    def _try_save_comparison_to_postgres(
        self,
        doc1_id: str,
        doc2_id: str,
        result: ComparisonResult,
        audit_mode: bool,
    ) -> None:
        try:
            import psycopg
        except ImportError:
            logger.debug("psycopg not available; skipping comparison_results DB save")
            return

        from grc_policy_server.core.config import settings as _settings

        db_url = _settings.database_url
        if not db_url:
            return

        result_json = result.model_dump_json()
        comparison_mode = "auditor_grade" if audit_mode else "simple"
        try:
            with psycopg.connect(db_url) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS comparison_results (
                        id BIGSERIAL PRIMARY KEY,
                        doc1_id TEXT NOT NULL,
                        doc2_id TEXT NOT NULL,
                        comparison_mode TEXT NOT NULL,
                        result_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
                conn.execute(
                    "INSERT INTO comparison_results"
                    " (doc1_id, doc2_id, comparison_mode, result_json)"
                    " VALUES (%s, %s, %s, %s::jsonb)",
                    (doc1_id, doc2_id, comparison_mode, result_json),
                )
                conn.commit()
        except Exception as exc:
            logger.warning("comparison_results DB save failed: %s", exc)

    def _save_compare_trace(
        self,
        *,
        doc1: Document,
        doc2: Document,
        left_nodes: list[dict],
        right_nodes: list[dict],
        matching: ClauseMatchingResult,
        grouped_alignments: list[tuple[str, list[dict], list[dict], float]],
        change_records: list[ChangeRecord],
        diffs: list[KeyDifference],
        language: str,
        llm_payload: dict,
        summary: str,
        follow_up_questions: list[str],
    ) -> None:
        if self.trace_store is None:
            return
        left_artifacts = self._load_debug_artifacts(doc1.id)
        right_artifacts = self._load_debug_artifacts(doc2.id)
        payload = {
            "doc1Id": doc1.id,
            "doc2Id": doc2.id,
            "language": language,
            "checkpoints": {
                "rawExtractedStructure": {
                    "doc1": self._raw_extraction_summary(left_artifacts),
                    "doc2": self._raw_extraction_summary(right_artifacts),
                },
                "normalizedNodeTree": {
                    "doc1NodeCounts": self._node_counts(left_nodes),
                    "doc2NodeCounts": self._node_counts(right_nodes),
                    "doc1Nodes": len(left_nodes),
                    "doc2Nodes": len(right_nodes),
                    "doc1HierarchyDepth": self._hierarchy_depth(left_nodes),
                    "doc2HierarchyDepth": self._hierarchy_depth(right_nodes),
                    "doc1SectionLabels": self._section_labels(left_nodes),
                    "doc2SectionLabels": self._section_labels(right_nodes),
                    "doc1NodeOrdering": self._node_ordering(left_nodes),
                    "doc2NodeOrdering": self._node_ordering(right_nodes),
                    "doc1DroppedOrMergedBlocks": self._dropped_or_merged_blocks(
                        left_artifacts
                    ),
                    "doc2DroppedOrMergedBlocks": self._dropped_or_merged_blocks(
                        right_artifacts
                    ),
                },
                "retrievalArtifacts": {
                    "doc1": self._retrieval_artifact_summary(
                        left_artifacts, left_nodes
                    ),
                    "doc2": self._retrieval_artifact_summary(
                        right_artifacts, right_nodes
                    ),
                },
                "alignmentResults": {
                    "matchedNodes": len(matching.matches),
                    "unmatchedLeft": len(matching.removed),
                    "unmatchedRight": len(matching.added),
                    "lowConfidenceMatches": sum(
                        1
                        for match in matching.matches
                        if match.distance > self.thresholds.max_match_distance
                    ),
                    "sectionMatches": matching.section_matches,
                    "matchTypes": self._match_type_counts(matching.matches),
                    "splitsDetected": sum(
                        1
                        for alignment_type, *_ in grouped_alignments
                        if alignment_type == "split"
                    ),
                    "mergesDetected": sum(
                        1
                        for alignment_type, *_ in grouped_alignments
                        if alignment_type == "merged"
                    ),
                },
                "diffRecords": {
                    "changeCounts": self._change_type_counts(change_records),
                    "numericChanges": sum(
                        len(record.numeric_changes) for record in change_records
                    ),
                    "tableChanges": sum(
                        len(record.table_changes) for record in change_records
                    ),
                    "movedClauses": sum(
                        1
                        for record in change_records
                        if record.alignment_type == "moved"
                    ),
                    "confidenceDistribution": [
                        record.confidence for record in change_records
                    ],
                    "changeRecords": [
                        record.to_trace_payload() for record in change_records
                    ],
                },
                "llmInputPayload": llm_payload,
                "finalSummary": {
                    "summary": summary,
                    "referencedChangeRecordIds": llm_payload.get(
                        "includedChangeRecordIds",
                        [],
                    ),
                    "omittedChangeRecordIds": llm_payload.get(
                        "omittedChangeRecordIds",
                        [],
                    ),
                    "citationCoverage": self._citation_coverage(diffs),
                    "followUpQuestions": follow_up_questions,
                },
            },
        }
        self.trace_store.save_trace(doc1_id=doc1.id, doc2_id=doc2.id, payload=payload)

    def _change_records_llm_payload(
        self,
        *,
        doc1: Document,
        doc2: Document,
        change_records: list[ChangeRecord],
        language: str,
    ) -> dict:
        record_payloads = [record.to_llm_payload() for record in change_records]
        grouped = self._group_records_by_chapter(record_payloads)
        payload = {
            "promptVersion": "structured-change-records-v2",
            "documentMetadata": {
                "doc1": doc1.model_dump(mode="json"),
                "doc2": doc2.model_dump(mode="json"),
            },
            "comparisonObjective": (
                "Summarize factual policy changes from canonical document nodes. "
                "Do not use retrieval chunks as comparison evidence."
            ),
            "mode": "general",
            "language": language or "unknown",
            "includedChangeRecordIds": [
                str(record.get("changeId") or "") for record in record_payloads
            ],
            "omittedChangeRecordIds": [],
            "groupedChangeRecordsByChapter": grouped,
            "changeRecords": record_payloads,
            "unresolvedAlignments": [
                record
                for record in record_payloads
                if float(record.get("confidence") or 0.0) < 0.5
            ],
        }
        payload_json = self._json_for_trace(payload)
        payload["tokenCounts"] = {
            "estimatedPayloadTokens": max(1, len(payload_json) // 4),
            "changeRecords": len(record_payloads),
        }
        return payload

    @staticmethod
    def _group_records_by_chapter(record_payloads: list[dict]) -> list[dict]:
        grouped: dict[str, list[dict]] = {}
        for record in record_payloads:
            section = str(record.get("section") or "Unknown Section")
            chapter = section.split(" / ", 1)[0] or "Unknown Section"
            grouped.setdefault(chapter, []).append(record)
        return [
            {"chapter": chapter, "changeRecords": records}
            for chapter, records in grouped.items()
        ]

    def _load_debug_artifacts(self, document_id: str) -> dict:
        loader = getattr(self.canonical_store, "load_debug_artifacts", None)
        if not callable(loader):
            return {}
        try:
            loaded = loader(document_id)
        except Exception:
            logger.exception(
                "failed to load compare debug artifacts document_id=%s", document_id
            )
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _raw_extraction_summary(self, artifacts: dict) -> dict:
        raw_docling = artifacts.get("rawDoclingJson")
        hierarchy = self._artifact_hierarchy(artifacts)
        nodes = hierarchy.get("nodes") if isinstance(hierarchy, dict) else []
        node_list = [node for node in nodes if isinstance(node, dict)]
        return {
            "rawDoclingStored": raw_docling is not None,
            "rawDoclingPath": artifacts.get("rawDoclingPath"),
            "normalizedTreePath": artifacts.get("normalizedTreePath"),
            "hierarchyPath": artifacts.get("hierarchyPath"),
            "extractedHeadings": [
                str(node.get("title") or node.get("section_path") or "")
                for node in node_list
                if str(node.get("node_type") or "") == "section"
            ],
            "blockOrder": [
                {
                    "nodeId": str(node.get("node_id") or ""),
                    "nodeType": str(node.get("node_type") or ""),
                    "page": node.get("page_number") or node.get("page_from"),
                    "order": node.get("ordinal") or node.get("order_index"),
                }
                for node in node_list
            ],
            "tablesFound": sum(
                1 for node in node_list if str(node.get("node_type") or "") == "table"
            ),
            "listsFound": sum(
                1 for node in node_list if self._looks_like_list_node(node)
            ),
            "pageAnchors": sorted(
                {
                    int(page)
                    for node in node_list
                    for page in [node.get("page_number") or node.get("page_from")]
                    if page is not None
                }
            ),
        }

    @staticmethod
    def _artifact_hierarchy(artifacts: dict) -> dict:
        hierarchy = artifacts.get("hierarchyJson")
        if isinstance(hierarchy, dict):
            return hierarchy
        normalized = artifacts.get("normalizedTreeJson")
        return normalized if isinstance(normalized, dict) else {}

    @staticmethod
    def _looks_like_list_node(node: dict) -> bool:
        if str(node.get("node_type") or "") == "list_item":
            return True
        metadata = (
            node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        )
        labels = " ".join(
            str(label).lower() for label in metadata.get("source_labels") or []
        )
        return "list" in labels or "bullet" in labels

    @staticmethod
    def _hierarchy_depth(nodes: list[dict]) -> int:
        return max(
            (
                len(node.get("heading_path") or node.get("lineage") or [])
                for node in nodes
            ),
            default=0,
        )

    @staticmethod
    def _section_labels(nodes: list[dict]) -> list[str]:
        return sorted(
            {
                str(node.get("section_label") or "")
                for node in nodes
                if str(node.get("section_label") or "").strip()
            }
        )

    def _node_ordering(self, nodes: list[dict]) -> list[dict]:
        return [
            {
                "nodeId": str(node.get("node_id") or node.get("chunk_id") or ""),
                "nodeType": str(node.get("node_type") or ""),
                "section": str(node.get("section_path") or ""),
                "page": node.get("page_number") or node.get("page"),
                "order": node.get("order_index") or node.get("chunk_index"),
            }
            for node in sorted(nodes, key=self._node_sort_key)
        ]

    @staticmethod
    def _dropped_or_merged_blocks(artifacts: dict) -> dict:
        hierarchy = RealDiffEngine._artifact_hierarchy(artifacts)
        metadata = hierarchy.get("metadata") if isinstance(hierarchy, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        return {
            "excludedNodes": int(metadata.get("excluded_nodes") or 0),
            "ocrNodes": int(metadata.get("ocr_nodes") or 0),
            "reportedNodeCounts": metadata.get("node_counts") or {},
        }

    def _retrieval_artifact_summary(self, artifacts: dict, nodes: list[dict]) -> dict:
        normalized = artifacts.get("normalizedTreeJson")
        retrieval = (
            normalized.get("retrievalArtifacts")
            if isinstance(normalized, dict)
            else None
        )
        if isinstance(retrieval, dict):
            return retrieval

        hierarchy = self._artifact_hierarchy(artifacts)
        hierarchy_nodes = hierarchy.get("nodes") if isinstance(hierarchy, dict) else []
        mapping = []
        if isinstance(hierarchy_nodes, list):
            mapping = [
                {
                    "chunkId": str(node.get("node_id") or ""),
                    "canonicalNodeId": str(node.get("node_id") or ""),
                    "parentId": node.get("parent_id"),
                    "nodeType": node.get("node_type"),
                    "sectionPath": node.get("section_path"),
                    "page": node.get("page_number"),
                }
                for node in hierarchy_nodes
                if isinstance(node, dict) and bool(node.get("indexable", False))
            ]

        if not mapping:
            mapping = [
                {
                    "chunkId": str(node.get("chunk_id") or node.get("node_id") or ""),
                    "canonicalNodeId": str(
                        node.get("canonical_node_id") or node.get("node_id") or ""
                    ),
                    "parentId": node.get("parent_id"),
                    "nodeType": node.get("node_type"),
                    "sectionPath": node.get("section_path"),
                    "page": node.get("page_number"),
                }
                for node in nodes
            ]
        return {
            "retrievalChunkCount": len(mapping),
            "chunkToNodeMapping": mapping,
        }

    @staticmethod
    def _citation_coverage(diffs: list[KeyDifference]) -> dict:
        total = len(diffs)
        if total == 0:
            return {
                "totalChanges": 0,
                "changesWithAnyCitation": 0,
                "changesWithBothCitations": 0,
                "coverageRatio": 1.0,
            }
        with_any = sum(1 for diff in diffs if diff.doc1Reference or diff.doc2Reference)
        with_both = sum(
            1 for diff in diffs if diff.doc1Reference and diff.doc2Reference
        )
        return {
            "totalChanges": total,
            "changesWithAnyCitation": with_any,
            "changesWithBothCitations": with_both,
            "coverageRatio": round(with_any / total, 4),
        }

    @staticmethod
    def _json_for_trace(payload: dict) -> str:
        try:
            import json

            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return str(payload)

    @staticmethod
    def _node_counts(nodes: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in nodes:
            node_type = str(node.get("node_type") or "unknown")
            counts[node_type] = counts.get(node_type, 0) + 1
        return counts

    @staticmethod
    def _match_type_counts(matches: list[ClauseMatch]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for match in matches:
            counts[match.matched_by] = counts.get(match.matched_by, 0) + 1
        return counts

    @staticmethod
    def _change_type_counts(records: list[ChangeRecord]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in records:
            counts[record.change_type] = counts.get(record.change_type, 0) + 1
        return counts

    # ------------------------------------------------------------------ #
    #  Caption-row helpers                                                #
    # ------------------------------------------------------------------ #

    def _has_caption_row(self, node: dict) -> bool:
        """Return True when a table's row 0 is a caption embedded as a table cell.

        Detected when row 0 contains exactly one cell that spans all (or all-but-one)
        columns and whose text matches the "Table N" / "Figure N" pattern.
        """
        cells = node.get("table_cells") or []
        num_cols = int(node.get("table_num_cols") or 0)
        if not cells or num_cols == 0:
            return False
        row0_cells = [c for c in cells if int(c.get("row", -1)) == 0]
        if len(row0_cells) != 1:
            return False
        cell = row0_cells[0]
        col_span = int(cell.get("col_span", 1))
        text = str(cell.get("text") or "").strip()
        return col_span >= max(1, num_cols - 1) and bool(_CAPTION_ROW_RE.match(text))

    def _normalize_cells_for_comparison(self, node: dict) -> list[dict]:
        """Return table cells with any caption row stripped and rows re-indexed."""
        cells = node.get("table_cells") or []
        if not cells or not self._has_caption_row(node):
            return cells
        result = []
        for c in cells:
            row = int(c.get("row", 0))
            if row == 0:
                continue
            result.append({**c, "row": row - 1})
        return result

    # ------------------------------------------------------------------ #
    #  Reference-only change detection                                    #
    # ------------------------------------------------------------------ #

    def _is_reference_only_change(self, left_text: str, right_text: str) -> bool:
        """Return True when the two texts differ only in table/figure/section reference numbers.

        Example: "See Table 2.1 for details." vs "See Table 1 for details."
        Both collapse to "See REF for details." → identical → reference-only.
        """
        if not left_text or not right_text:
            return False
        left_norm = _REF_TOKEN_RE.sub("REF", left_text.lower())
        right_norm = _REF_TOKEN_RE.sub("REF", right_text.lower())
        left_norm = re.sub(r"\s+", " ", left_norm).strip()
        right_norm = re.sub(r"\s+", " ", right_norm).strip()
        return left_norm == right_norm

    # ------------------------------------------------------------------ #
    #  Severity computation                                               #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_cell_for_severity(text: str) -> str:
        """Normalise cell text for semantic equality testing.

        Strips formatting characters (hyphens, newlines, semicolons), collapses
        whitespace, and lowercases so that cosmetic differences like
        "Temperature:" vs "temperature" or "value;\n" vs "value" are identical.
        """
        t = text.strip().lower()
        # Strip formatting chars that carry no semantic weight
        t = re.sub(r"[-–—\n\r;]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        t = t.rstrip(":.,")
        return t

    def _table_content_bag(self, node: dict) -> frozenset[str]:
        """Return the set of normalised non-empty cell texts (position-independent)."""
        cells = self._normalize_cells_for_comparison(node)
        _norm = self._normalize_cell_for_severity
        return frozenset(v for c in cells if (v := _norm(str(c.get("text", "")))) != "")

    @staticmethod
    def _table_jaccard(left_bag: frozenset[str], right_bag: frozenset[str]) -> float:
        """Jaccard similarity of two cell-content bags."""
        if not left_bag and not right_bag:
            return 1.0
        intersection = len(left_bag & right_bag)
        union = len(left_bag | right_bag)
        return intersection / union if union else 1.0

    def _table_severity(self, left: dict, right: dict) -> str:
        """Severity for MODIFIED table pairs — content-set (Jaccard) primary, position-overlap secondary.

        Rules:
          low    – Jaccard ≥ 0.90 OR position overlap ≥ 0.85 (caption/formatting only)
          medium – Jaccard ≥ 0.60 OR position overlap ≥ 0.50
          high   – everything else
        """
        left_cells = self._normalize_cells_for_comparison(left)
        right_cells = self._normalize_cells_for_comparison(right)

        if not left_cells or not right_cells:
            # No cell data – fall back to markdown/text similarity
            left_text = str(left.get("markdown_text") or left.get("text") or "")
            right_text = str(right.get("markdown_text") or right.get("text") or "")
            if not left_text or not right_text:
                return "high"
            ratio = SequenceMatcher(None, left_text, right_text).ratio()
            if ratio >= 0.90:
                return "low"
            if ratio >= 0.60:
                return "medium"
            return "high"

        left_bag = self._table_content_bag(left)
        right_bag = self._table_content_bag(right)
        jaccard = self._table_jaccard(left_bag, right_bag)

        # Rule 1: content-set nearly identical → low regardless of position/dimensions
        if jaccard >= 0.90:
            return "low"

        # Rule 2: fall back to position-based overlap for medium/high boundary
        _norm = self._normalize_cell_for_severity
        left_cell_map = {
            (int(c.get("row", 0)), int(c.get("col", 0))): _norm(str(c.get("text", "")))
            for c in left_cells
        }
        right_cell_map = {
            (int(c.get("row", 0)), int(c.get("col", 0))): _norm(str(c.get("text", "")))
            for c in right_cells
        }
        all_positions = set(left_cell_map.keys()) | set(right_cell_map.keys())
        if all_positions:
            fuzzy_match_count = 0
            for pos in all_positions:
                lv = left_cell_map.get(pos, "")
                rv = right_cell_map.get(pos, "")
                if lv == rv and lv:
                    fuzzy_match_count += 1
                elif lv and rv and SequenceMatcher(None, lv, rv).ratio() >= 0.85:
                    fuzzy_match_count += 1
            position_overlap = fuzzy_match_count / len(all_positions)
        else:
            position_overlap = 0.0

        if position_overlap >= 0.85:
            return "low"
        if jaccard >= 0.60 or position_overlap >= 0.50:
            return "medium"
        return "high"

    def _meaning_change(self, left: dict, right: dict, language: str = "") -> str:
        if left.get("node_type") == "table" or right.get("node_type") == "table":
            return self._table_meaning_change(left, right)
        left_src = self._source_text(left)
        right_src = self._source_text(right)
        # Formatting-only changes (newlines, hyphens, semicolons) carry no semantic weight.
        if is_formatting_only_change(left_src, right_src):
            return "unchanged"
        comparison = compare_clause_meaning(
            self._node_meaning(left),
            self._node_meaning(right),
            language,
        )
        if comparison.obligation_change != "unchanged":
            return comparison.obligation_change
        if comparison.score < 0.75 and not is_cosmetic_text_change(left_src, right_src):
            return "changed"
        return "unchanged"

    def _table_meaning_change(self, left: dict, right: dict) -> str:
        """Detect structural/content differences in tables.

        Caption rows are stripped before comparison so a table where the caption
        is embedded as row 0 is treated identically to one where it is external.

        Uses content-set (Jaccard) similarity — when ≥ 90% of cell content is shared
        between documents (regardless of position/dimension), the meaning is unchanged.
        This handles cases where caption-as-row or minor structural reordering causes
        apparent dimension mismatches without actual semantic difference.
        """
        left_bag = self._table_content_bag(left)
        right_bag = self._table_content_bag(right)

        # Content-set nearly identical → semantically unchanged
        if self._table_jaccard(left_bag, right_bag) >= 0.90:
            return "unchanged"

        return "modified"

    def _render_cells_preview(self, cells: list[dict], max_rows: int = 5) -> str:
        """Render first `max_rows` rows of table cells as pipe-delimited text."""
        from collections import defaultdict

        rows: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for c in cells:
            row = int(c.get("row", c.get("row_index", 0)))
            col = int(c.get("col", c.get("col_index", 0)))
            text = str(c.get("text") or "").strip()
            rows[row].append((col, text))
        if not rows:
            return ""
        lines = []
        for row_idx in sorted(rows.keys())[:max_rows]:
            cols = sorted(rows[row_idx], key=lambda x: x[0])
            line = " | ".join(v for _, v in cols)
            lines.append(line)
        remaining = len(rows) - max_rows
        if remaining > 0:
            lines.append(f"... ({remaining} more rows)")
        return "\n".join(lines)

    def _format_table_content(self, node: dict) -> str:
        """Format table content for display in diffs."""
        title = node.get("title") or node.get("table_normalized_caption") or ""
        num_rows = node.get("table_num_rows", 0)
        num_cols = node.get("table_num_cols", 0)

        # Subtract caption row from displayed count so both docs show the same
        # number when one embeds its caption as a row and the other does not.
        if self._has_caption_row(node):
            num_rows = max(0, (num_rows or 0) - 1)

        # 1. Try markdown first (best quality)
        markdown = node.get("markdown_text") or ""
        if markdown:
            lines = markdown.strip().split("\n")[:8]
            return "\n".join(lines) + ("..." if len(markdown.split("\n")) > 8 else "")

        # 2. Try cell preview from table_cells
        cells = node.get("table_cells") or []
        if cells:
            preview = self._render_cells_preview(cells, max_rows=5)
            if preview:
                header = f"{title}\n" if title else ""
                dim = (
                    f"({num_rows} rows × {num_cols} cols)\n"
                    if num_rows and num_cols
                    else ""
                )
                return f"{header}{dim}{preview}"

        # 3. Dimensions-only fallback
        if num_rows and num_cols:
            table_desc = f"Table ({num_rows} rows × {num_cols} cols)"
            return f"{title}: {table_desc}" if title else table_desc

        return self._short(str(node.get("text") or ""), n=120)

    async def _enrich_nodes_with_semantics(
        self,
        nodes: list[dict],
        force_re_extract: bool = False,
        language: str = "",
    ) -> list[dict]:
        """Apply rule-based semantic enrichment — no LLM calls."""
        enriched = [dict(node) for node in nodes]
        for node in enriched:
            if not node.get("clean_text"):
                node["clean_text"] = clean_policy_text(str(node.get("text") or ""))
            if node.get("node_type") not in TEXT_COMPARISON_NODE_TYPES:
                continue
            if not force_re_extract and any(
                node.get(field)
                for field in ("obligation", "subject", "action", "object", "condition")
            ):
                continue
            text = str(node.get("text") or "").strip()
            if not text:
                continue
            meaning = extract_clause_meaning(text)
            node["obligation"] = meaning.obligation
            node["subject"] = meaning.subject
            node["action"] = meaning.action
            node["object"] = meaning.object
            node["condition"] = meaning.condition
        return enriched

    async def _detect_document_language(self, nodes: list[dict]) -> str:
        """Rule-based language detection from node text."""
        import re

        sample_texts = []
        for node in nodes[:5]:
            text = str(node.get("text") or "").strip()
            if text:
                sample_texts.append(text)
            if len(" ".join(sample_texts)) > 500:
                break
        if not sample_texts:
            return ""
        sample = " ".join(sample_texts)[:500].lower()
        tokens = re.findall(r"[a-zA-ZÀ-ÿ]+", sample)
        if not tokens:
            return ""
        lexicons = {
            "en": {"the", "and", "shall", "must", "should", "policy", "is", "are"},
            "de": {"der", "die", "das", "und", "muss", "müssen", "soll", "sollen"},
            "fr": {"le", "la", "les", "et", "doit", "doivent", "sont"},
        }
        scores: dict[str, int] = {code: 0 for code in lexicons}
        for token in tokens:
            for code, lexicon in lexicons.items():
                if token in lexicon:
                    scores[code] += 1
        if any(ch in sample for ch in "äöüß"):
            scores["de"] += 2
        if any(ch in sample for ch in "àâçéèêëîïôûùüÿœæ"):
            scores["fr"] += 2
        best = max(scores, key=scores.get)
        if scores[best] == 0:
            return ""
        winners = [code for code, sc in scores.items() if sc == scores[best]]
        return best if len(winners) == 1 else ""

    def _node_meaning(self, node: dict) -> ClauseMeaning:
        obligation = str(node.get("obligation") or "")
        subject = str(node.get("subject") or "")
        action = str(node.get("action") or "")
        obj = str(node.get("object") or "")
        condition = str(node.get("condition") or "")
        if obligation or subject or action or obj or condition:
            return ClauseMeaning(obligation, subject, action, obj, condition)
        return extract_clause_meaning(str(node.get("text") or ""))

    def _citation_from_neo4j_or_fallback(self, chunk: dict) -> DocumentReference:
        chunk_id = chunk.get("chunk_id")
        if chunk_id and self.neo4j is not None:
            citation = self.neo4j.get_chunk_citation(chunk_id=str(chunk_id))
            if citation:
                source_text = self._reference_source_text(chunk) or str(
                    citation.get("sourceText") or ""
                )
                citation["sourceText"] = source_text
                return DocumentReference(**citation)
        page = chunk.get("page_number")
        if page is None:
            page = chunk.get("page")
        source_text = self._reference_source_text(chunk)
        section = str(chunk.get("section_path") or "")
        # Replace dimension-only fallback labels like "3 rows × 4 columns" with
        # a meaningful ancestor heading so references are readable in the report.
        if not section or re.fullmatch(
            r"\d+\s*(?:rows?\s*[×x]\s*\d+\s*col|col[^\s]*)", section, re.IGNORECASE
        ):
            heading_path = chunk.get("heading_path") or []
            ancestor = next(
                (str(h) for h in reversed(heading_path) if str(h).strip()), ""
            )
            section = ancestor or "[Untitled Table]"
        node_id = str(chunk.get("node_id") or "") or None
        text_hash = (
            chunk.get("pure_text_hash")
            or (chunk.get("metadata") or {}).get("pure_text_hash")
            or None
        )
        bbox_refs = chunk.get("bbox_refs") or []
        bbox = bbox_refs[0] if bbox_refs else None
        return DocumentReference(
            section=section,
            page=int(page or 0),
            lineStart=chunk.get("line_start"),
            lineEnd=chunk.get("line_end"),
            sourceText=source_text,
            nodeId=node_id,
            textHash=text_hash,
            bbox=bbox,
        )

    def _reference_source_text(self, chunk: dict) -> str:
        if chunk.get("node_type") == "table":
            markdown = str(chunk.get("markdown_text") or "").strip()
            if markdown:
                return markdown
        return str(chunk.get("text") or "")

    def _is_non_semantic_node(self, node: dict) -> bool:
        """Return True if the node's content has no semantic diff value."""
        text = str(
            node.get("clean_text") or clean_policy_text(str(node.get("text") or ""))
        ).strip()
        node_type = str(node.get("node_type") or "clause")
        return is_non_semantic_content(text) or is_docling_orphan_fragment(
            text, node_type
        )

    def _short(self, text: str, n: int = 90) -> str:
        t = " ".join((text or "").split())
        return t if len(t) <= n else t[:n] + "..."

    def _action_plan(self, diffs: List[KeyDifference]) -> List[ActionItem]:
        actions: List[ActionItem] = []
        for diff in diffs:
            if diff.impact == "High":
                actions.append(
                    ActionItem(
                        priority="High",
                        action=f"Assess compliance impact of {diff.changeType.lower()} items in {diff.section}",
                        timeline="60 days",
                        owner="Compliance Team",
                    )
                )
        return actions[:5]

    async def _follow_ups(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        diffs: List[KeyDifference],
        language: str,
    ) -> List[str]:
        sampled_diffs = random_diff_subset(diffs, max_items=10)
        if not sampled_diffs:
            return [
                "Are there any material compliance requirement changes between these versions?",
                "Which sections require immediate policy updates?",
            ]

        try:
            questions = await self.llm.generate_followups(
                doc1_name=doc1_name,
                doc2_name=doc2_name,
                key_differences=sampled_diffs,
                max_questions=4,
                language=language,
            )
            questions = [question.strip() for question in questions if question.strip()]
            if questions:
                return questions[:4]
        except Exception:
            logger.exception("failed to generate LLM follow-up questions")

        return [
            f"What controls or evidence must be updated for {diff.section}?"
            for diff in sampled_diffs[:4]
        ]

    async def _two_step_summary(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
        language: str,
    ) -> str:
        if not key_differences:
            return "No material differences were detected."

        explanations = await self._explain_differences(
            key_differences, language=language
        )

        try:
            return await self.llm.summarize_explanations(
                doc1_name=doc1_name,
                doc2_name=doc2_name,
                explanations=explanations,
                language=language,
            )
        except Exception:
            logger.exception("failed two-step summary aggregation, falling back")

        return await self.llm.summarize_changes(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            key_differences=key_differences,
            language=language,
        )

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
        if not change_records:
            return "No material differences were detected."

        summarize_change_records = getattr(self.llm, "summarize_change_records", None)
        if callable(summarize_change_records):
            try:
                return await summarize_change_records(
                    doc1_name=doc1_name,
                    doc2_name=doc2_name,
                    change_record_payload=llm_payload,
                    language=language,
                )
            except Exception:
                logger.exception(
                    "failed structured change-record summary, falling back"
                )

        return await self._two_step_summary(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            key_differences=key_differences,
            language=language,
        )

    async def _explain_differences(
        self,
        key_differences: List[KeyDifference],
        *,
        language: str,
    ) -> list[dict[str, str]]:
        capped = key_differences[: self.max_diffs]
        tasks = [
            self.llm.summarize_diff(
                old_text=self._diff_text(diff.doc1Reference, diff.doc1Content),
                new_text=self._diff_text(diff.doc2Reference, diff.doc2Content),
                section=diff.section,
                language=language,
            )
            for diff in capped
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        explanations: list[dict[str, str]] = []
        for diff, result in zip(capped, results, strict=False):
            if isinstance(result, Exception):
                explanation = (
                    f"{diff.changeType} change in {diff.section}: "
                    "content changed based on canonical comparison."
                )
            else:
                explanation = str(result or "").strip()
            explanations.append(
                {
                    "changeType": diff.changeType,
                    "section": diff.section,
                    "nodeType": diff.nodeType,
                    "impact": diff.impact,
                    "changeSeverity": diff.changeSeverity,
                    "changes": [
                        change.model_dump(mode="json") for change in diff.changes
                    ],
                    "doc1Content": diff.doc1Content or "",
                    "doc2Content": diff.doc2Content or "",
                    "doc1Citation": (
                        diff.doc1Reference.model_dump(mode="json")
                        if diff.doc1Reference
                        else None
                    ),
                    "doc2Citation": (
                        diff.doc2Reference.model_dump(mode="json")
                        if diff.doc2Reference
                        else None
                    ),
                    "explanation": explanation,
                }
            )
        return explanations

    def _diff_text(
        self,
        reference: DocumentReference | None,
        fallback: str | None,
    ) -> str:
        if reference and reference.sourceText:
            return reference.sourceText
        return str(fallback or "")

    def _section_accuracy_metrics(
        self, matches: list[ClauseMatch]
    ) -> list[SectionAccuracyMetrics]:
        sections: dict[str, list[float]] = {}
        for match in matches:
            section = str(
                match.right.get("section_path")
                or match.left.get("section_path")
                or "Unknown Section"
            )
            sections.setdefault(section, []).append(match.distance)

        metrics: list[SectionAccuracyMetrics] = []
        for section, distances in sections.items():
            count = len(distances)
            avg_distance = sum(distances) / count
            avg_score = 1.0 - avg_distance
            high = sum(1 for d in distances if d <= self.thresholds.unchanged_distance)
            med = sum(
                1
                for d in distances
                if self.thresholds.unchanged_distance
                < d
                <= self.thresholds.max_match_distance
            )
            low = count - high - med
            confidence = (high * 1.0 + med * 0.7 + low * 0.3) / count

            metrics.append(
                SectionAccuracyMetrics(
                    section=section,
                    avg_match_distance=round(avg_distance, 4),
                    avg_match_score=round(avg_score, 4),
                    match_count=count,
                    confidence=round(confidence, 4),
                )
            )

        return sorted(metrics, key=lambda m: m.section)

    # ------------------------------------------------------------------ #
    #  Phase 16 — No-Change Coverage                                      #
    # ------------------------------------------------------------------ #

    def _readable_section_label(self, node: dict) -> str:
        """Return a short human-readable section label for a comparison node."""
        section_path = node.get("section_path") or node.get("heading_path")
        if isinstance(section_path, list):
            parts = [str(s).strip() for s in section_path if str(s).strip()]
            if len(parts) >= 2:
                return " > ".join(parts[-2:])
            if parts:
                return parts[-1]
        if isinstance(section_path, str) and section_path.strip():
            return section_path.strip()
        title = str(node.get("title") or "").strip()
        if title:
            return title
        return str(node.get("text") or "").strip()[:60] or "Unknown Section"

    def _compute_no_change_coverage(
        self,
        matching: ClauseMatchingResult,
        change_records: list[ChangeRecord],
    ) -> list[dict]:
        """Phase 16: sections checked and found unchanged.

        Groups high-confidence unchanged matches by readable section label and
        computes a confidence level based on the match distance distribution.
        Only sections with no change records are included.
        """
        changed_sections: set[str] = {r.section for r in change_records}

        section_data: dict[str, dict[str, int]] = {}
        for match in matching.matches:
            if match.distance >= self.thresholds.unchanged_distance:
                continue
            section = self._readable_section_label(match.left)
            if any(section in cs or cs in section for cs in changed_sections):
                continue
            if section not in section_data:
                section_data[section] = {"very_high": 0, "high": 0, "total": 0}
            section_data[section]["total"] += 1
            if match.distance < 0.08:
                section_data[section]["very_high"] += 1
            else:
                section_data[section]["high"] += 1

        coverage: list[dict] = []
        for section, data in sorted(section_data.items()):
            total = data["total"]
            if total == 0:
                continue
            h_ratio = (data["very_high"] + data["high"]) / total
            if h_ratio >= 0.80:
                confidence = "High"
            elif h_ratio >= 0.50:
                confidence = "Medium"
            else:
                confidence = "Low"
            coverage.append(
                {"section": section, "confidence": confidence, "nodeCount": total}
            )

        return coverage

    def _compute_accuracy_metrics(
        self, matches: list[ClauseMatch]
    ) -> ComparisonAccuracyMetrics:
        """Confidence levels based on match distance:
        - High: distance <= 0.20 (very similar clauses)
        - Medium: distance <= 0.35 (reasonably similar)
        - Low: distance > 0.35 (weak match)
        """
        if not matches:
            return ComparisonAccuracyMetrics(
                avg_match_distance=0.0,
                avg_match_score=None,
                high_confidence_matches=0,
                medium_confidence_matches=0,
                low_confidence_matches=0,
                total_matches=0,
                overall_confidence=0.0,
                confidence_breakdown={
                    "stable_id": 0,
                    "section_stable_id": 0,
                    "section_alignment": 0,
                    "vector_search": 0,
                },
                section_metrics=[],
            )

        distances = [m.distance for m in matches]
        avg_distance = sum(distances) / len(distances)
        avg_score = 1.0 - avg_distance

        high_conf = sum(1 for d in distances if d <= self.thresholds.unchanged_distance)
        medium_conf = sum(
            1
            for d in distances
            if self.thresholds.unchanged_distance
            < d
            <= self.thresholds.max_match_distance
        )
        low_conf = sum(1 for d in distances if d > self.thresholds.max_match_distance)

        breakdown: dict[str, int] = {}
        for m in matches:
            breakdown[m.matched_by] = breakdown.get(m.matched_by, 0) + 1

        weighted_confidence = (
            high_conf * 1.0 + medium_conf * 0.7 + low_conf * 0.3
        ) / len(matches)

        return ComparisonAccuracyMetrics(
            avg_match_distance=round(avg_distance, 4),
            avg_match_score=round(avg_score, 4),
            high_confidence_matches=high_conf,
            medium_confidence_matches=medium_conf,
            low_confidence_matches=low_conf,
            total_matches=len(matches),
            overall_confidence=round(weighted_confidence, 4),
            confidence_breakdown=breakdown,
            section_metrics=self._section_accuracy_metrics(matches),
        )

    def _table_content_for_llm(self, node: dict | None) -> str | None:
        """Return the best available text representation of a table node for LLM prompts."""
        if not node:
            return None
        # Prefer markdown (human-readable structure), fall back to normalized text projection
        return (
            str(node.get("markdown_text") or "").strip()
            or str(node.get("normalized_table_text") or "").strip()
            or str(node.get("comparison_text") or "").strip()
            or None
        )

    @staticmethod
    def _col_header(table_change: dict, headers: list[str]) -> str:
        """Extract the column name for a table_change entry from the node's header list.

        table_change["location"] is "Row N, Col M" (1-indexed). We parse M and look
        it up in headers (0-indexed). Falls back to empty string when unparseable.
        """
        location = str(table_change.get("location") or "")
        import re as _re

        m = _re.search(r"Col\s+(\d+)", location, _re.IGNORECASE)
        if m:
            col_1idx = int(m.group(1))
            if 1 <= col_1idx <= len(headers):
                return headers[col_1idx - 1]
        return ""

    async def _populate_markdown_diff_summaries(
        self,
        diffs: List[KeyDifference],
        change_records: list[ChangeRecord] | None = None,
        *,
        language: str = "",
    ) -> None:
        """Generate markdownDiffSummary for every diff in parallel via LLM."""
        _records: list[ChangeRecord | None] = list(change_records or [])
        sem = asyncio.Semaphore(4)

        async def _generate_one(diff: KeyDifference, i: int):
            record: ChangeRecord | None = _records[i] if i < len(_records) else None
            left_node = record.left_nodes[0] if (record and record.left_nodes) else None
            right_node = (
                record.right_nodes[0] if (record and record.right_nodes) else None
            )
            is_table = diff.nodeType == "table"
            async with sem:
                if is_table and record and record.table_changes:
                    headers = list(
                        (left_node or {}).get("table_headers")
                        or (right_node or {}).get("table_headers")
                        or []
                    )
                    enriched = [
                        {**tc, "header": self._col_header(tc, headers)}
                        for tc in record.table_changes
                    ]
                    return await self.llm.explain_table_diff(
                        old_markdown=self._table_content_for_llm(left_node),
                        new_markdown=self._table_content_for_llm(right_node),
                        changed_cells=enriched,
                        change_type=diff.changeType,
                        language=language,
                    )
                return await self.llm.generate_markdown_diff_summary(
                    node_type=diff.nodeType,
                    change_type=diff.changeType,
                    doc1_source_text=(
                        diff.doc1Reference.sourceText if diff.doc1Reference else None
                    ),
                    doc2_source_text=(
                        diff.doc2Reference.sourceText if diff.doc2Reference else None
                    ),
                    doc1_table_content=self._table_content_for_llm(left_node)
                    if is_table
                    else None,
                    doc2_table_content=self._table_content_for_llm(right_node)
                    if is_table
                    else None,
                    language=language,
                )

        results = await asyncio.gather(
            *[_generate_one(diff, i) for i, diff in enumerate(diffs)],
            return_exceptions=True,
        )
        for diff, result in zip(diffs, results, strict=False):
            if isinstance(result, Exception):
                logger.warning(
                    "markdownDiffSummary generation failed for section=%s: %s",
                    diff.section,
                    result,
                )
            else:
                diff.markdownDiffSummary = str(result or "").strip() or None

    def _extract_changes(
        self, left: dict, right: dict, node_type: str
    ) -> List[ChangeDetail]:
        """Extract specific changes between two nodes for UI highlighting."""
        if node_type == "table":
            return self._extract_table_changes(left, right)
        return self._extract_text_changes(left, right)

    def _extract_text_changes(self, left: dict, right: dict) -> List[ChangeDetail]:
        """Extract line-level changes between two text chunks."""
        changes: List[ChangeDetail] = []
        left_text = str(left.get("text") or "")
        right_text = str(right.get("text") or "")

        # Split into lines/items (handle bullet points)
        left_lines = [
            line.strip()
            for line in left_text.replace(" - ", "\n- ").split("\n")
            if line.strip()
        ]
        right_lines = [
            r.strip()
            for r in right_text.replace(" - ", "\n- ").split("\n")
            if r.strip()
        ]

        left_set = set(left_lines)
        right_set = set(right_lines)

        # Find removed lines
        for line in left_lines:
            if line not in right_set:
                # Check if it was modified (similar line exists)
                modified_match = self._find_similar_line(line, right_lines)
                if modified_match:
                    changes.append(
                        ChangeDetail(
                            type="modified",
                            text=line,
                            oldValue=line,
                            newValue=modified_match,
                        )
                    )
                else:
                    changes.append(
                        ChangeDetail(
                            type="removed",
                            text=line,
                        )
                    )

        # Find added lines (excluding those already matched as modified)
        modified_new_values = {c.newValue for c in changes if c.type == "modified"}
        for line in right_lines:
            if line not in left_set and line not in modified_new_values:
                changes.append(
                    ChangeDetail(
                        type="added",
                        text=line,
                    )
                )

        return changes

    def _find_similar_line(self, line: str, candidates: list[str]) -> str | None:
        """Find a similar line in candidates (for detecting modifications)."""
        from difflib import SequenceMatcher

        line_lower = line.lower()
        for candidate in candidates:
            ratio = SequenceMatcher(None, line_lower, candidate.lower()).ratio()
            if ratio > 0.6:  # More than 60% similar
                return candidate
        return None

    def _extract_table_changes(self, left: dict, right: dict) -> List[ChangeDetail]:
        """Extract cell-level changes between two tables.

        Caption rows are stripped and rows re-indexed before comparison so that
        a table with an embedded caption does not appear to have an extra row.
        """
        changes: List[ChangeDetail] = []

        left_rows = max(
            0,
            (left.get("table_num_rows") or 0)
            - (1 if self._has_caption_row(left) else 0),
        )
        right_rows = max(
            0,
            (right.get("table_num_rows") or 0)
            - (1 if self._has_caption_row(right) else 0),
        )

        # Dimension changes
        if left_rows != right_rows:
            if right_rows > left_rows:
                changes.append(
                    ChangeDetail(
                        type="added",
                        text=f"{right_rows - left_rows} row(s) added",
                        location=f"Rows {left_rows + 1}-{right_rows}",
                    )
                )
            else:
                changes.append(
                    ChangeDetail(
                        type="removed",
                        text=f"{left_rows - right_rows} row(s) removed",
                    )
                )

        # Cell-level changes using caption-normalised cells
        left_cells = self._normalize_cells_for_comparison(left)
        right_cells = self._normalize_cells_for_comparison(right)

        left_cell_map = {
            (c.get("row", 0), c.get("col", 0)): str(c.get("text", "")).strip()
            for c in left_cells
        }
        right_cell_map = {
            (c.get("row", 0), c.get("col", 0)): str(c.get("text", "")).strip()
            for c in right_cells
        }

        # Find modified cells (same position, different content)
        for pos, left_val in left_cell_map.items():
            right_val = right_cell_map.get(pos)
            if right_val is not None and left_val != right_val:
                row, col = pos
                changes.append(
                    ChangeDetail(
                        type="modified",
                        text="Cell changed",
                        oldValue=left_val,
                        newValue=right_val,
                        location=f"Row {row + 1}, Col {col + 1}",
                    )
                )

        # Find cells in new rows
        for pos, right_val in right_cell_map.items():
            if pos not in left_cell_map and right_val:
                row, col = pos
                if row >= left_rows:  # New row
                    changes.append(
                        ChangeDetail(
                            type="added",
                            text=right_val,
                            location=f"Row {row + 1}, Col {col + 1}",
                        )
                    )

        return changes
