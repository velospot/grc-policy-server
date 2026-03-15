from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from grc_policy_server.core.logging import logging
from grc_policy_server.models.schemas import (
    ActionItem,
    ComparisonAccuracyMetrics,
    ComparisonResult,
    Document,
    DocumentReference,
    KeyDifference,
    SectionAccuracyMetrics,
)
from grc_policy_server.services.comparision.clause_matcher import (
    ClauseMatch,
    ClauseMatcher,
    MatchThresholds,
)
from grc_policy_server.services.comparision.policy_semantics import (
    ClauseMeaning,
    clean_policy_text,
    compare_clause_meaning,
    extract_clause_meaning,
)
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.llm.ollama_client import OllamaClient
from grc_policy_server.services.vector.weaviate_client import WeaviateClient
from grc_policy_server.utils.hashing import normalize_whitespace

logger = logging.getLogger(__name__)


def build_markdown_lookup(nodes: list[dict]) -> dict[str, str]:
    """Build chunk_id -> markdown_text lookup from fetched nodes.

    This hashmap is built at runtime from Weaviate data and used
    for LLM prompts where markdown formatting improves accuracy.
    """
    return {
        str(node.get("chunk_id") or ""): str(node.get("markdown_text") or "")
        for node in nodes
        if node.get("chunk_id") and node.get("markdown_text")
    }


def impact_from_distance(
    distance: Optional[float],
    change_type: str,
    *,
    obligation_change: str = "unchanged",
) -> str:
    if change_type == "ADDED":
        return "High"
    if change_type == "REMOVED":
        return "High"
    if obligation_change == "weakened":
        return "Critical"
    if obligation_change == "strengthened":
        return "High"
    if distance is None:
        return "High"
    if distance > 0.45:
        return "Critical"
    if distance > 0.35:
        return "High"
    if distance > 0.25:
        return "Medium"
    return "Low"


@dataclass
class RealDiffEngine:
    weaviate: WeaviateClient
    neo4j: Neo4jClient | None
    llm: OllamaClient
    thresholds: MatchThresholds = MatchThresholds()
    topk: int = 5
    max_diffs: int = 40

    async def compare(
        self,
        doc1: Document,
        doc2: Document,
        force_re_extract: bool = False,
    ) -> ComparisonResult:
        left_nodes = self.weaviate.fetch_chunks_by_document(doc1.id)
        right_nodes = self.weaviate.fetch_chunks_by_document(doc2.id)

        # Build markdown lookup for LLM prompts (runtime hashmap)
        left_markdown = build_markdown_lookup(left_nodes)
        right_markdown = build_markdown_lookup(right_nodes)
        logger.info(
            "markdown lookup built: left=%d right=%d",
            len(left_markdown),
            len(right_markdown),
        )

        # Detect language from first document's text for better LLM accuracy
        language = await self._detect_document_language(left_nodes)
        logger.info("detected document language=%s", language or "unknown")

        left_nodes = await self._enrich_nodes_with_semantics(
            left_nodes, force_re_extract=force_re_extract, language=language
        )
        right_nodes = await self._enrich_nodes_with_semantics(
            right_nodes, force_re_extract=force_re_extract, language=language
        )
        logger.info(
            "compare left_nodes=%s right_nodes=%s", len(left_nodes), len(right_nodes)
        )

        matcher = ClauseMatcher(
            search_fn=self.weaviate.search_section_in_document,
            thresholds=self.thresholds,
            topk=self.topk,
            language=language,
        )
        matching = matcher.match(
            left_nodes=left_nodes,
            right_nodes=right_nodes,
            target_document_id=doc2.id,
        )

        diffs: List[KeyDifference] = []

        for match in matching.matches:
            meaning_change = self._meaning_change(match.left, match.right, language)
            if (
                match.distance <= self.thresholds.unchanged_distance
                and meaning_change == "unchanged"
            ):
                continue
            left_ref = self._citation_from_neo4j_or_fallback(match.left)
            right_ref = self._citation_from_neo4j_or_fallback(match.right)
            diffs.append(
                KeyDifference(
                    changeType="MODIFIED",
                    section=(
                        left_ref.section
                        if left_ref
                        else str(match.left.get("section_path") or "Unknown Section")
                    ),
                    doc1Content=self._short(str(match.left.get("text") or "")),
                    doc2Content=self._short(str(match.right.get("text") or "")),
                    impact=impact_from_distance(
                        match.distance,
                        "MODIFIED",
                        obligation_change=meaning_change,
                    ),
                    doc1Reference=left_ref,
                    doc2Reference=right_ref,
                )
            )

        for left_node in matching.removed:
            left_ref = self._citation_from_neo4j_or_fallback(left_node)
            diffs.append(
                KeyDifference(
                    changeType="REMOVED",
                    section=(
                        left_ref.section
                        if left_ref
                        else str(left_node.get("section_path") or "Unknown Section")
                    ),
                    doc1Content=self._short(str(left_node.get("text") or "")),
                    doc2Content=None,
                    impact=impact_from_distance(None, "REMOVED"),
                    doc1Reference=left_ref,
                    doc2Reference=None,
                )
            )

        for right_node in matching.added:
            right_ref = self._citation_from_neo4j_or_fallback(right_node)
            diffs.append(
                KeyDifference(
                    changeType="ADDED",
                    section=(
                        right_ref.section
                        if right_ref
                        else str(right_node.get("section_path") or "Unknown Section")
                    ),
                    doc1Content=None,
                    doc2Content=self._short(str(right_node.get("text") or "")),
                    impact=impact_from_distance(None, "ADDED"),
                    doc1Reference=None,
                    doc2Reference=right_ref,
                )
            )

        diffs.sort(
            key=lambda diff: (
                ("Critical", "High", "Medium", "Low").index(diff.impact)
                if diff.impact in ("Critical", "High", "Medium", "Low")
                else 2
            )
        )

        only_changed_diffs: List[KeyDifference] = []

        for index, diff in enumerate(diffs):
            if diff.doc1Reference and diff.doc2Reference is not None:
                if normalize_whitespace(
                    diff.doc1Reference.sourceText
                ) != normalize_whitespace(diff.doc2Reference.sourceText):
                    only_changed_diffs.append(diff)

        diffs = diffs[: self.max_diffs]

        summary = await self.llm.summarize_changes(
            doc1_name=doc1.name,
            doc2_name=doc2.name,
            key_differences=only_changed_diffs,
            language=language,
        )
        accuracy_metrics = self._compute_accuracy_metrics(matching.matches)

        return ComparisonResult(
            summary=summary,
            keyDifferences=only_changed_diffs,
            actionPlan=self._action_plan(only_changed_diffs),
            followUpQuestions=self._follow_ups(only_changed_diffs),
            accuracyMetrics=accuracy_metrics,
        )

    def _meaning_change(self, left: dict, right: dict, language: str = "") -> str:
        return compare_clause_meaning(
            self._node_meaning(left),
            self._node_meaning(right),
            language,
        ).obligation_change

    async def _enrich_nodes_with_semantics(
        self,
        nodes: list[dict],
        force_re_extract: bool = False,
        language: str = "",
    ) -> list[dict]:
        enriched = [dict(node) for node in nodes]
        indexes: list[int] = []
        texts: list[str] = []
        markdown_texts: list[str] = []

        for index, node in enumerate(enriched):
            if not node.get("clean_text"):
                node["clean_text"] = clean_policy_text(str(node.get("text") or ""))
            if node.get("node_type") != "clause":
                continue
            # Skip if already has semantics, unless force_re_extract is True
            if not force_re_extract and any(
                node.get(field)
                for field in ("obligation", "subject", "action", "object", "condition")
            ):
                continue
            text = str(node.get("text") or "").strip()
            if not text:
                continue
            indexes.append(index)
            texts.append(text)
            # Use markdown if available, otherwise fall back to plain text
            markdown_texts.append(str(node.get("markdown_text") or text))

        if not texts:
            return enriched

        for index, meaning in zip(
            indexes,
            await self.llm.extract_policy_meanings(
                texts=texts,
                markdown_texts=markdown_texts,
                language=language,
            ),
            strict=False,
        ):
            for field, value in meaning.items():
                enriched[index][field] = str(value or "")

        return enriched

    async def _detect_document_language(self, nodes: list[dict]) -> str:
        """Detect language from first few chunks of text."""
        sample_texts = []
        for node in nodes[:5]:
            text = str(node.get("text") or "").strip()
            if text:
                sample_texts.append(text)
            if len(" ".join(sample_texts)) > 500:
                break
        if not sample_texts:
            return ""
        sample = " ".join(sample_texts)[:500]
        return await self.llm.detect_language(sample)

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
                return DocumentReference(**citation)
        page = chunk.get("page_number")
        if page is None:
            page = chunk.get("page")
        return DocumentReference(
            section=str(chunk.get("section_path") or "Unknown Section"),
            page=int(page or 0),
            lineStart=chunk.get("line_start"),
            lineEnd=chunk.get("line_end"),
            sourceText=str(chunk.get("text") or ""),
        )

    def _short(self, text: str, n: int = 90) -> str:
        t = " ".join((text or "").split())
        return t if len(t) <= n else t[:n] + "..."

    def _action_plan(self, diffs: List[KeyDifference]) -> List[ActionItem]:
        actions: List[ActionItem] = []
        for diff in diffs:
            if diff.impact in ("Critical", "High"):
                actions.append(
                    ActionItem(
                        priority="Immediate" if diff.impact == "Critical" else "High",
                        action=f"Assess compliance impact of {diff.changeType.lower()} items in {diff.section}",
                        timeline="30 days" if diff.impact == "Critical" else "60 days",
                        owner="Compliance Team",
                    )
                )
        return actions[:5]

    def _follow_ups(self, diffs: List[KeyDifference]) -> List[str]:
        questions = []
        for diff in diffs[:6]:
            questions.append(
                f"What controls/evidence need updates due to {diff.changeType.lower()} changes in {diff.section}?"
            )
        if not questions:
            questions = [
                "Are there any material compliance requirement changes between these versions?",
                "Which sections require immediate policy updates?",
            ]
        return questions

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
