from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx
from qdrant_client import QdrantClient as _QdrantSDK
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    UpdateStatus,
    VectorParams,
)

from grc_policy_server.core.config import settings
from grc_policy_server.core.logging import logging

logger = logging.getLogger(__name__)


class QdrantVectorClient:
    """Qdrant-backed vector store — drop-in replacement for WeaviateClient.

    All public methods preserve the same signatures and return shapes so
    callers require no changes beyond import/param renaming.
    """

    def __init__(self) -> None:
        self.collection_name = settings.qdrant_collection
        self._client = _QdrantSDK(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=30,
        )
        self._schema_ensured = False

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _embed(self, text: str) -> list[float]:
        """Call Ollama synchronously to vectorize text (mirrors text2vec-ollama)."""
        payload = {"model": settings.ollama_embed_model, "input": text or " "}
        r = httpx.post(
            settings.ollama_embedding_url.rstrip("/") + "/api/embed",
            json=payload,
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()["embeddings"][0]

    def _ensure_collection(self) -> None:
        if self._schema_ensured:
            return
        if not self._client.collection_exists(self.collection_name):
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=settings.qdrant_vector_size,
                    distance=Distance.COSINE,
                ),
            )
            for field_name in ("document_id", "node_type", "chunk_id"):
                self._client.create_payload_index(
                    self.collection_name,
                    field_name,
                    PayloadSchemaType.KEYWORD,
                )
        self._schema_ensured = True

    def _doc_filter(
        self,
        document_id: str,
        node_types: list[str] | None = None,
    ) -> Filter:
        conds: list[FieldCondition] = [
            FieldCondition(key="document_id", match=MatchValue(value=document_id))
        ]
        if node_types:
            conds.append(FieldCondition(key="node_type", match=MatchAny(any=node_types)))
        return Filter(must=conds)

    # ------------------------------------------------------------------
    # Public API (same interface as WeaviateClient)
    # ------------------------------------------------------------------

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        self._ensure_collection()
        points: list[PointStruct] = []
        for chunk in chunks:
            text = str(
                chunk.get("comparison_text")
                or chunk.get("clean_text")
                or chunk.get("text")
                or ""
            )
            try:
                vector = self._embed(text)
            except Exception:
                logger.warning(
                    "qdrant embed failed chunk_id=%s — skipping vector upsert for this chunk",
                    chunk.get("chunk_id"),
                    exc_info=True,
                )
                continue
            uid = str(uuid5(NAMESPACE_URL, str(chunk["chunk_id"])))
            points.append(PointStruct(id=uid, vector=vector, payload=dict(chunk)))

        if points:
            self._client.upsert(collection_name=self.collection_name, points=points)

    def delete_chunks_by_document(self, document_id: str) -> int:
        result = self._client.delete(
            collection_name=self.collection_name,
            points_selector=Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
            ),
        )
        return 1 if result.status == UpdateStatus.COMPLETED else 0

    def fetch_chunks_by_document(self, document_id: str) -> list[dict[str, Any]]:
        points, _ = self._client.scroll(
            collection_name=self.collection_name,
            scroll_filter=Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
            ),
            limit=10000,
            with_payload=True,
            with_vectors=False,
        )
        return [p.payload for p in points if p.payload]

    def semantic_search_in_document(
        self,
        *,
        query_vector: list[float],
        target_document_id: str,
        limit: int = 3,
        node_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        hits = self._client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            query_filter=self._doc_filter(target_document_id, node_types),
            limit=limit,
            with_payload=True,
        )
        return [
            {**h.payload, "_score": h.score, "_distance": 1.0 - h.score}
            for h in hits
            if h.payload
        ]

    def hybrid_search_in_document(
        self,
        *,
        query_string: str,
        target_document_id: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        vector = self._embed(query_string)
        return self.semantic_search_in_document(
            query_vector=vector,
            target_document_id=target_document_id,
            limit=limit,
        )

    def search_section_in_document(
        self,
        *,
        query_string: str,
        query_text: str,
        target_document_id: str,
        limit: int = 3,
        node_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        vector = self._embed(query_text or query_string)
        return self.semantic_search_in_document(
            query_vector=vector,
            target_document_id=target_document_id,
            limit=limit,
            node_types=node_types,
        )
