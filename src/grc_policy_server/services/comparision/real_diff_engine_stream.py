from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Dict, List, Optional, Tuple

from grc_policy_server.models.schemas import (
    ActionItem,
    Document,
    DocumentReference,
    KeyDifference,
)
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.llm.ollama_client import OllamaClient
from grc_policy_server.services.vector.weaviate_client import WeaviateClient


@dataclass(frozen=True)
class DiffThresholds:
    max_match_distance: float = 0.50
    unchanged_distance: float = 0.15


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
    thresholds: DiffThresholds = DiffThresholds()
    topk: int = 3

    async def compare_stream(
        self, doc1: Document, doc2: Document
    ) -> AsyncIterator[Dict]:
        """
        Yields dict events:
          {type: "progress", ...}
          {type: "diff", item: KeyDifference}
          {type: "done", summary, actionPlan, followUpQuestions}
        """

        yield {"type": "progress", "stage": "load_chunks"}

        a_chunks = self.weaviate.fetch_chunks_by_document(doc1.id)
        b_chunks = self.weaviate.fetch_chunks_by_document(doc2.id)

        yield {
            "type": "progress",
            "stage": "chunks_loaded",
            "doc1_chunks": len(a_chunks),
            "doc2_chunks": len(b_chunks),
        }

        # Build candidate edges and one-to-one matching (greedy)
        # Collect candidate edges: (distance, a_id, b_id, a_obj, b_obj)
        yield {"type": "progress", "stage": "matching_start"}

        candidate_edges: List[Tuple[float, str, str, dict, dict]] = []

        for idx, a in enumerate(a_chunks):
            text_a = (a.get("text") or "").strip()
            if not text_a:
                continue

            # embed doc1 chunk
            v = await self.llm.embed(text_a)

            matches = self.weaviate.semantic_search_in_document(
                query_vector=v,
                target_document_id=doc2.id,
                limit=self.topk,
            )

            for m in matches:
                dist = m.get("_distance")
                if dist is None or dist > self.thresholds.max_match_distance:
                    continue

                a_id = a.get("chunk_id") or ""
                b_id = m.get("chunk_id") or ""
                if not a_id or not b_id:
                    continue

                candidate_edges.append((float(dist), a_id, b_id, a, m))

            # Stream progress every ~50 chunks
            if idx % 50 == 0 and idx > 0:
                yield {
                    "type": "progress",
                    "stage": "embedding_matching",
                    "processed": idx,
                }

        candidate_edges.sort(key=lambda x: x[0])

        matched_a: Dict[str, Tuple[float, dict, dict]] = {}
        matched_b: Dict[str, str] = {}

        for dist, a_id, b_id, a_obj, b_obj in candidate_edges:
            if a_id in matched_a or b_id in matched_b:
                continue
            matched_a[a_id] = (dist, a_obj, b_obj)
            matched_b[b_id] = a_id

        yield {"type": "progress", "stage": "classification_start"}

        # Emit MODIFIED + REMOVED as streaming diffs
        streamed_diffs: List[KeyDifference] = []

        for a in a_chunks:
            a_id = a.get("chunk_id") or ""
            if not a_id:
                continue

            if a_id not in matched_a:
                kd = await self._make_removed(a)
                streamed_diffs.append(kd)
                yield {"type": "diff", "item": kd.model_dump()}
                continue

            dist, a_obj, b_obj = matched_a[a_id]
            if dist <= self.thresholds.unchanged_distance:
                continue

            kd = await self._make_modified(a_obj, b_obj, dist)
            streamed_diffs.append(kd)
            yield {"type": "diff", "item": kd.model_dump()}

        # Emit ADDED (doc2 chunks not matched)
        for b in b_chunks:
            b_id = b.get("chunk_id") or ""
            if not b_id:
                continue
            if b_id in matched_b:
                continue

            kd = await self._make_added(b)
            streamed_diffs.append(kd)
            yield {"type": "diff", "item": kd.model_dump()}

        yield {"type": "progress", "stage": "finalizing"}

        # End payload: summary + actions + follow-ups
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
            "actionPlan": [a.model_dump() for a in action_plan],
            "followUpQuestions": followups,
        }

    async def _make_modified(self, a: dict, b: dict, dist: float) -> KeyDifference:
        a_ref = self._citation_from_neo4j_or_fallback(a.get("chunk_id"), a)
        b_ref = self._citation_from_neo4j_or_fallback(b.get("chunk_id"), b)

        return KeyDifference(
            changeType="MODIFIED",
            section=(
                a_ref.section if a_ref else (a.get("section_path") or "Unknown Section")
            ),
            doc1Content=self._short(a.get("text", "")),
            doc2Content=self._short(b.get("text", "")),
            impact=impact_from("MODIFIED", dist),
            doc1Reference=a_ref,
            doc2Reference=b_ref,
        )

    async def _make_removed(self, a: dict) -> KeyDifference:
        a_ref = self._citation_from_neo4j_or_fallback(a.get("chunk_id"), a)
        return KeyDifference(
            changeType="REMOVED",
            section=(
                a_ref.section if a_ref else (a.get("section_path") or "Unknown Section")
            ),
            doc1Content=self._short(a.get("text", "")),
            doc2Content=None,
            impact=impact_from("REMOVED", None),
            doc1Reference=a_ref,
            doc2Reference=None,
        )

    async def _make_added(self, b: dict) -> KeyDifference:
        b_ref = self._citation_from_neo4j_or_fallback(b.get("chunk_id"), b)
        return KeyDifference(
            changeType="ADDED",
            section=(
                b_ref.section if b_ref else (b.get("section_path") or "Unknown Section")
            ),
            doc1Content=None,
            doc2Content=self._short(b.get("text", "")),
            impact=impact_from("ADDED", None),
            doc1Reference=None,
            doc2Reference=b_ref,
        )

    def _citation_from_neo4j_or_fallback(
        self, chunk_id: Optional[str], fallback: dict
    ) -> Optional[DocumentReference]:
        if chunk_id:
            c = self.neo4j.get_chunk_citation(chunk_id=chunk_id)
            if c:
                return DocumentReference(**c)

        # fallback to Weaviate metadata if Neo4j missing (not ideal, but prevents crashes)
        return DocumentReference(
            section=fallback.get("section_path", "Unknown Section"),
            page=int(fallback.get("page") or 0),
            lineStart=fallback.get("line_start"),
            lineEnd=fallback.get("line_end"),
            sourceText=fallback.get("text", "") or "",
        )

    def _short(self, text: str, n: int = 90) -> str:
        t = " ".join((text or "").split())
        return t if len(t) <= n else t[:n] + "..."

    def _action_plan(self, diffs: List[KeyDifference]) -> List[ActionItem]:
        actions: List[ActionItem] = []
        for d in diffs:
            if d.impact in ("Critical", "High"):
                actions.append(
                    ActionItem(
                        priority="Immediate" if d.impact == "Critical" else "High",
                        action=f"Assess controls impacted by {d.changeType.lower()} changes in {d.section}",
                        timeline="30 days" if d.impact == "Critical" else "60 days",
                        owner="Compliance Team",
                    )
                )
        return actions[:5]

    def _followups(self, diffs: List[KeyDifference]) -> List[str]:
        qs = []
        for d in diffs[:6]:
            qs.append(
                f"What controls/evidence need updates due to {d.changeType.lower()} changes in {d.section}?"
            )
        if not qs:
            qs = [
                "Are there any material compliance requirement changes between these versions?",
                "Which sections require immediate policy updates?",
            ]
        return qs
