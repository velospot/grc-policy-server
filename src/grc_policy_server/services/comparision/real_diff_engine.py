from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from grc_policy_server.core.logging import logging
from grc_policy_server.models.schemas import (
    ActionItem,
    ComparisonResult,
    Document,
    DocumentReference,
    KeyDifference,
)
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.llm.ollama_client import OllamaClient
from grc_policy_server.services.vector.weaviate_client import WeaviateClient


@dataclass(frozen=True)
class DiffThresholds:
    # distance: smaller = more similar
    max_match_distance: float = 0.50  # beyond this: treat as no match
    unchanged_distance: float = 0.15  # below this: basically same
    modified_distance: float = 0.25  # above this (and matched): MODIFIED


def impact_from_distance(distance: Optional[float], change_type: str) -> str:
    # Conservative defaults for compliance settings
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


logger = logging.getLogger(__name__)


@dataclass
class RealDiffEngine:
    weaviate: WeaviateClient
    neo4j: Neo4jClient
    llm: OllamaClient
    thresholds: DiffThresholds = DiffThresholds()
    topk: int = 5
    max_diffs: int = 40  # cap for UI sanity

    async def compare(self, doc1: Document, doc2: Document) -> ComparisonResult:
        a_chunks = self.weaviate.fetch_chunks_by_document(doc1.id)
        b_chunks = self.weaviate.fetch_chunks_by_document(doc2.id)
        logger.info("a_chunks count = %s", len(a_chunks))
        logger.info("b_chunks count = %s", len(b_chunks))
        # Build candidate match edges: (distance, a_chunk_id, b_chunk_id)
        candidate_edges: List[Tuple[float, str, str, dict, dict]] = []

        # Index B chunks by chunk_id for fast lookup
        b_by_id: Dict[str, dict] = {
            c.get("chunk_id") or c.get("chunk_id", ""): c for c in b_chunks
        }
        # NOTE: ensure your ingestion sets chunk_id property and uses it consistently

        for a in a_chunks:
            text_a = (a.get("text") or "").strip()
            a_section = (a.get("section_path") or "").strip()
            if not text_a or "..." not in text_a:
                continue

            # embed A chunk text
            # v = self.llm.embed(text_a)

            # Search ONLY inside doc2
            matches = self.weaviate.search_section_in_document(
                query_string=a_section,
                query_text=text_a,
                target_document_id=doc2.id,
                limit=self.topk,
            )

            for m in matches:
                dist = m.get("_distance")
                if dist is None:
                    continue
                if dist > self.thresholds.max_match_distance:
                    continue

                a_id = a.get("chunk_id") or ""
                b_id = m.get("chunk_id") or ""
                if not a_id or not b_id:
                    continue

                candidate_edges.append((float(dist), a_id, b_id, a, m))

        # Greedy one-to-one matching by smallest distance
        candidate_edges.sort(key=lambda x: x[0])

        matched_a: Dict[str, Tuple[float, dict, dict]] = {}
        matched_b: Dict[str, str] = {}  # b_id -> a_id

        for dist, a_id, b_id, a_obj, b_obj in candidate_edges:
            if a_id in matched_a:
                continue
            if b_id in matched_b:
                continue
            matched_a[a_id] = (dist, a_obj, b_obj)
            matched_b[b_id] = a_id

        # Determine MODIFIED vs unchanged vs REMOVED
        diffs: List[KeyDifference] = []

        # A side: removed or modified
        for a in a_chunks:
            a_id = a.get("chunk_id") or ""
            a_section = a.get("section_path") or ""
            if not a_id:
                continue

            if a_id not in matched_a:
                # REMOVED
                # section = self._resolve_section(a)
                diffs.append(
                    KeyDifference(
                        changeType="REMOVED",
                        section=a_section,
                        doc1Content=self._short(a.get("text", "")),
                        doc2Content=None,
                        impact=impact_from_distance(None, "REMOVED"),
                        doc1Reference=self._ref_from_chunk(a),
                        doc2Reference=None,
                    )
                )
                continue

            dist, a_obj, b_obj = matched_a[a_id]

            # unchanged: optional ignore
            if dist <= self.thresholds.unchanged_distance:
                continue

            # MODIFIED
            # section = self._resolve_section(a_obj)
            diffs.append(
                KeyDifference(
                    changeType="MODIFIED",
                    section=a_section,
                    doc1Content=self._short(a_obj.get("text", "")),
                    doc2Content=self._short(b_obj.get("text", "")),
                    impact=impact_from_distance(dist, "MODIFIED"),
                    doc1Reference=self._ref_from_chunk(a_obj),
                    doc2Reference=self._ref_from_chunk(b_obj),
                )
            )

        # B side: added (not matched by any A)
        for b in b_chunks:
            b_id = b.get("chunk_id") or ""
            b_section = b.get("section_path") or ""
            if not b_id:
                continue
            if b_id in matched_b:
                continue

            # section = self._resolve_section(b)
            diffs.append(
                KeyDifference(
                    changeType="ADDED",
                    section=b_section,
                    doc1Content=None,
                    doc2Content=self._short(b.get("text", "")),
                    impact=impact_from_distance(None, "ADDED"),
                    doc1Reference=None,
                    doc2Reference=self._ref_from_chunk(b),
                )
            )

        # Cap for UI; prefer Critical/High first
        diffs.sort(
            key=lambda d: (
                ("Critical", "High", "Medium", "Low").index(d.impact)
                if d.impact in ("Critical", "High", "Medium", "Low")
                else 2
            )
        )
        diffs = diffs[: self.max_diffs]

        # Summary should be derived from diffs only (never full docs)
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

    def _resolve_section(self, chunk: dict) -> str:
        # Prefer Neo4j if you have chunk_id nodes, else fallback to metadata
        cid = chunk.get("chunk_id")
        if cid:
            try:
                s = self.neo4j.resolve_section_path(chunk_id=cid)
                if s and s != "Unknown Section":
                    return s
            except Exception:
                pass
        return chunk.get("section_path") or "Unknown Section"

    def _short(self, text: str, n: int = 90) -> str:
        t = " ".join((text or "").split())
        return t if len(t) <= n else t[:n] + "..."

    def _ref_from_chunk(self, ch: dict) -> DocumentReference:
        return DocumentReference(
            section=ch.get("section_path", "Unknown Section"),
            page=int(ch.get("page") or 0),
            lineStart=ch.get("line_start"),
            lineEnd=ch.get("line_end"),
            sourceText=ch.get("text", "") or "",
        )

    def _action_plan(self, diffs: List[KeyDifference]) -> List[ActionItem]:
        actions: List[ActionItem] = []
        for d in diffs:
            if d.impact in ("Critical", "High"):
                actions.append(
                    ActionItem(
                        priority="Immediate" if d.impact == "Critical" else "High",
                        action=f"Assess compliance impact of {d.changeType.lower()} items in {d.section}",
                        timeline="30 days" if d.impact == "Critical" else "60 days",
                        owner="Compliance Team",
                    )
                )
        return actions[:5]

    def _follow_ups(self, diffs: List[KeyDifference]) -> List[str]:
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
