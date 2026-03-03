from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Dict, List, Optional

from grc_policy_server.models.schemas import (
    ActionItem,
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


def impact_from(change_type: str, distance: Optional[float]) -> str:
    if change_type in ("ADDED", "REMOVED"):
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
class RealDiffEngineStream:
    weaviate: WeaviateClient
    neo4j: Neo4jClient
    llm: OllamaClient
    thresholds: MatchThresholds = MatchThresholds()
    topk: int = 5

    async def compare_stream(
        self, doc1: Document, doc2: Document
    ) -> AsyncIterator[Dict]:
        yield {"type": "progress", "stage": "load_chunks"}

        left_nodes = self.weaviate.fetch_chunks_by_document(doc1.id)
        right_nodes = self.weaviate.fetch_chunks_by_document(doc2.id)

        yield {
            "type": "progress",
            "stage": "chunks_loaded",
            "doc1_chunks": len(left_nodes),
            "doc2_chunks": len(right_nodes),
        }
        yield {"type": "progress", "stage": "matching_start"}

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

        yield {"type": "progress", "stage": "classification_start"}

        streamed_diffs: List[KeyDifference] = []
        for match in matching.matches:
            if match.distance <= self.thresholds.unchanged_distance:
                continue
            diff = await self._make_modified(match.left, match.right, match.distance)
            streamed_diffs.append(diff)
            yield {"type": "diff", "item": diff.model_dump()}

        for left_node in matching.removed:
            diff = await self._make_removed(left_node)
            streamed_diffs.append(diff)
            yield {"type": "diff", "item": diff.model_dump()}

        for right_node in matching.added:
            diff = await self._make_added(right_node)
            streamed_diffs.append(diff)
            yield {"type": "diff", "item": diff.model_dump()}

        yield {"type": "progress", "stage": "finalizing"}

        summary = await self.llm.summarize_changes(
            doc1_name=doc1.name,
            doc2_name=doc2.name,
            key_differences=streamed_diffs,
        )

        action_plan = self._action_plan(streamed_diffs)
        followups = self._followups(streamed_diffs)

        yield {
            "type": "done",
            "summary": summary,
            "actionPlan": [action.model_dump() for action in action_plan],
            "followUpQuestions": followups,
        }

    async def _make_modified(self, left: dict, right: dict, dist: float) -> KeyDifference:
        left_ref = self._citation_from_neo4j_or_fallback(left.get("chunk_id"), left)
        right_ref = self._citation_from_neo4j_or_fallback(right.get("chunk_id"), right)
        return KeyDifference(
            changeType="MODIFIED",
            section=(
                left_ref.section if left_ref else (left.get("section_path") or "Unknown Section")
            ),
            doc1Content=self._short(str(left.get("text") or "")),
            doc2Content=self._short(str(right.get("text") or "")),
            impact=impact_from("MODIFIED", dist),
            doc1Reference=left_ref,
            doc2Reference=right_ref,
        )

    async def _make_removed(self, left: dict) -> KeyDifference:
        left_ref = self._citation_from_neo4j_or_fallback(left.get("chunk_id"), left)
        return KeyDifference(
            changeType="REMOVED",
            section=(
                left_ref.section if left_ref else (left.get("section_path") or "Unknown Section")
            ),
            doc1Content=self._short(str(left.get("text") or "")),
            doc2Content=None,
            impact=impact_from("REMOVED", None),
            doc1Reference=left_ref,
            doc2Reference=None,
        )

    async def _make_added(self, right: dict) -> KeyDifference:
        right_ref = self._citation_from_neo4j_or_fallback(right.get("chunk_id"), right)
        return KeyDifference(
            changeType="ADDED",
            section=(
                right_ref.section if right_ref else (right.get("section_path") or "Unknown Section")
            ),
            doc1Content=None,
            doc2Content=self._short(str(right.get("text") or "")),
            impact=impact_from("ADDED", None),
            doc1Reference=None,
            doc2Reference=right_ref,
        )

    def _citation_from_neo4j_or_fallback(
        self, chunk_id: Optional[str], fallback: dict
    ) -> Optional[DocumentReference]:
        if chunk_id:
            citation = self.neo4j.get_chunk_citation(chunk_id=str(chunk_id))
            if citation:
                return DocumentReference(**citation)

        return DocumentReference(
            section=fallback.get("section_path", "Unknown Section"),
            page=int(fallback.get("page_number") or fallback.get("page") or 0),
            lineStart=fallback.get("line_start"),
            lineEnd=fallback.get("line_end"),
            sourceText=fallback.get("text", "") or "",
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
                        action=f"Assess controls impacted by {diff.changeType.lower()} changes in {diff.section}",
                        timeline="30 days" if diff.impact == "Critical" else "60 days",
                        owner="Compliance Team",
                    )
                )
        return actions[:5]

    def _followups(self, diffs: List[KeyDifference]) -> List[str]:
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
