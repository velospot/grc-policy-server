from typing import List

from grc_policy_server.models.schemas import (
    ActionItem,
    ComparisonResult,
    DocumentReference,
    KeyDifference,
)
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.llm.ollama_client import OllamaClient
from grc_policy_server.services.vector.weaviate_client import WeaviateClient


class RealDiffEngine:
    def __init__(self):
        self.weaviate = WeaviateClient()
        self.graph = Neo4jClient()
        self.llm = OllamaClient()

    def classify_impact(self, similarity: float) -> str:
        if similarity < 0.65:
            return "Critical"
        if similarity < 0.75:
            return "High"
        if similarity < 0.85:
            return "Medium"
        return "Low"

    def _shorten(self, text: str, limit: int = 120) -> str:
        text = text.strip().replace("\n", " ")
        return text[:limit] + "..." if len(text) > limit else text

    def _to_reference(self, chunk: dict) -> DocumentReference:
        return DocumentReference(
            section=chunk["section_path"],
            page=chunk.get("page", 0),
            lineStart=chunk.get("line_start"),
            lineEnd=chunk.get("line_end"),
            sourceText=chunk["text"],
        )

    def _generate_action_plan(self, diffs: List[KeyDifference]) -> List[ActionItem]:
        actions = []

        for diff in diffs:
            if diff.impact in ("Critical", "High"):
                actions.append(
                    ActionItem(
                        priority="Immediate" if diff.impact == "Critical" else "High",
                        action=f"Address changes in {diff.section}",
                        timeline="30 days" if diff.impact == "Critical" else "60 days",
                        owner="Compliance Team",
                    )
                )

        return actions[:5]  # cap noise

    def _generate_questions(self, diffs: List[KeyDifference]) -> List[str]:
        return [
            f"What controls are impacted by changes in {d.section}?" for d in diffs[:4]
        ]

    async def compare(self, doc1, doc2) -> ComparisonResult:
        """
        doc1, doc2 are Document (frontend contract objects)
        """

        # 1. Load all chunks for doc1
        doc1_chunks = self.weaviate.fetch_chunks_by_document(doc1.id)

        key_differences: List[KeyDifference] = []

        # 2. For each chunk in doc1, find best semantic match in doc2
        for chunk in doc1_chunks:
            embedding = chunk["embedding"]

            matches = self.weaviate.semantic_search(
                embedding=embedding,
                document_id=doc2.id,
                limit=1,
            )

            if not matches:
                continue  # removed section (handled later)

            best = matches[0]
            similarity = best["similarity"]

            if similarity > 0.9:
                continue  # unchanged, skip

            impact = self.classify_impact(similarity)

            # 3. Structural grounding via Neo4j
            section_path = self.graph.resolve_section_path(chunk["chunk_id"])

            # 4. LLM interpretation (bounded, local)
            summary = await self.llm.summarize_diff(
                old=chunk["text"],
                new=best["text"],
            )

            key_differences.append(
                KeyDifference(
                    section=section_path,
                    doc1Content=self._shorten(chunk["text"]),
                    doc2Content=self._shorten(best["text"]),
                    impact=impact,
                    doc1Reference=self._to_reference(chunk),
                    doc2Reference=self._to_reference(best),
                )
            )

        # 5. High-level summary (LLM over structured diffs)
        summary_text = await self.llm.summarize_changes(
            doc1.name,
            doc2.name,
            key_differences,
        )

        # 6. Action plan (rule-based + LLM polish)
        action_plan = self._generate_action_plan(key_differences)

        return ComparisonResult(
            summary=summary_text,
            keyDifferences=key_differences,
            actionPlan=action_plan,
            followUpQuestions=self._generate_questions(key_differences),
        )
