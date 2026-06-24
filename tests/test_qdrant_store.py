from __future__ import annotations

from grc_policy_server.services.vector.qdrant_store import QdrantVectorClient


class _StubQdrantSdk:
    def __init__(self) -> None:
        self.upsert_calls = []

    def upsert(self, *, collection_name, points):
        self.upsert_calls.append({"collection_name": collection_name, "points": points})


def _client() -> QdrantVectorClient:
    client = object.__new__(QdrantVectorClient)
    client.collection_name = "PolicyChunk"
    client._client = _StubQdrantSdk()
    client._schema_ensured = True
    client._embedding_unavailable_reason = None
    client._search_embedding_unavailable_reason = None
    client._ensure_collection = lambda: None  # type: ignore[method-assign]
    return client


def test_qdrant_upsert_chunks_skips_document_vectors_after_embedding_failure():
    client = _client()
    embed_calls = {"count": 0}

    def _failing_embed(text: str):
        embed_calls["count"] += 1
        raise RuntimeError("embedding endpoint unavailable")

    client._embed = _failing_embed  # type: ignore[method-assign]

    client.upsert_chunks(
        [
            {"chunk_id": "chunk-1", "text": "first"},
            {"chunk_id": "chunk-2", "text": "second"},
        ]
    )

    assert embed_calls["count"] == 1
    assert client._embedding_unavailable_reason == "embedding endpoint unavailable"
    assert client._client.upsert_calls == []


def test_qdrant_upsert_chunks_writes_points_when_embedding_available():
    client = _client()
    client._embed = lambda text: [0.1, 0.2, 0.3]  # type: ignore[method-assign]

    client.upsert_chunks([{"chunk_id": "chunk-1", "text": "first"}])

    assert len(client._client.upsert_calls) == 1
    assert client._client.upsert_calls[0]["collection_name"] == "PolicyChunk"
    assert len(client._client.upsert_calls[0]["points"]) == 1


def test_qdrant_search_returns_empty_after_embedding_failure():
    client = _client()
    embed_calls = {"count": 0}

    def _failing_embed(text: str):
        embed_calls["count"] += 1
        raise RuntimeError("embedding endpoint unavailable")

    client._embed = _failing_embed  # type: ignore[method-assign]

    first = client.search_section_in_document(
        query_string="limit",
        query_text="limit",
        target_document_id="doc-1",
    )
    second = client.search_section_in_document(
        query_string="margin",
        query_text="margin",
        target_document_id="doc-1",
    )

    assert first == []
    assert second == []
    assert embed_calls["count"] == 1
    assert client._search_embedding_unavailable_reason == "embedding endpoint unavailable"
