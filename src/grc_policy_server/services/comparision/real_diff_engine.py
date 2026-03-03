from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from grc_policy_server.core.logging import logging
from grc_policy_server.models.schemas import (
    ActionItem,
    ComparisonResult,
    Document,
    DocumentReference,
    KeyDifference,
)
from grc_policy_server.services.comparision.clause_matcher import (
    ClauseMatcher,
    MatchThresholds,
)
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.llm.ollama_client import OllamaClient
from grc_policy_server.services.vector.weaviate_client import WeaviateClient

logger = logging.getLogger(__name__)


def impact_from_distance(distance: Optional[float], change_type: str) -> str:
    if change_type == "ADDED":
        return "High"
    if change_type == "REMOVED":
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
    neo4j: Neo4jClient
    llm: OllamaClient
    thresholds: MatchThresholds = MatchThresholds()
    topk: int = 5
    max_diffs: int = 40

    async def compare(self, doc1: Document, doc2: Document) -> ComparisonResult:
        left_nodes = self.weaviate.fetch_chunks_by_document(doc1.id)
        right_nodes = self.weaviate.fetch_chunks_by_document(doc2.id)
        logger.info("compare left_nodes=%s right_nodes=%s", len(left_nodes), len(right_nodes))

        matcher = ClauseMatcher(
            search_fn=self.weaviate.search_section_in_document,
            thresholds=self.thresholds,
            topk=self.topk,
        )
        matching = matcher.match(
            left_nodes=left_nodes,
            right_nodes=right_nodes,
            target_document_id=doc2.id,
        )

        diffs: List[KeyDifference] = []

        for match in matching.matches:
            if match.distance <= self.thresholds.unchanged_distance:
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
                    impact=impact_from_distance(match.distance, "MODIFIED"),
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
        diffs = diffs[: self.max_diffs]

        summary = await self.llm.summarize_changes(
            doc1_name=doc1.name,
            doc2_name=doc2.name,
            key_differences=diffs,
        )

        return ComparisonResult(
            summary=summary,
            keyDifferences=diffs,
            actionPlan=self._action_plan(diffs),
            followUpQuestions=self._follow_ups(diffs),
        )

    def _citation_from_neo4j_or_fallback(self, chunk: dict) -> DocumentReference:
        chunk_id = chunk.get("chunk_id")
        if chunk_id:
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
