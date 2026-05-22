"""
Ports — abstract interfaces that decouple the domain from infrastructure.
All infrastructure adapters implement one of these protocols.
The core domain never imports from infrastructure layers.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Object Storage Port
# ---------------------------------------------------------------------------


@runtime_checkable
class ObjectStoragePort(Protocol):
    def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload bytes; return the canonical object key."""
        ...

    def download(self, key: str) -> bytes:
        """Download object by key."""
        ...

    def delete(self, key: str) -> None:
        ...

    def exists(self, key: str) -> bool:
        ...

    def get_presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        ...


# ---------------------------------------------------------------------------
# Vector Store Port
# ---------------------------------------------------------------------------


@runtime_checkable
class VectorStorePort(Protocol):
    def upsert_chunks(self, items: list[dict[str, Any]]) -> None:
        ...

    def upsert_nodes(self, items: list[dict[str, Any]]) -> None:
        ...

    def similarity_search(
        self,
        collection: str,
        query_text: str,
        top_k: int,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        ...

    def delete_by_version(self, collection: str, version_id: str) -> None:
        ...

    def is_available(self) -> bool:
        """Health check — allows graceful degradation."""
        ...


# ---------------------------------------------------------------------------
# LLM Gateway Port
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMGatewayPort(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.1,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Return plain-text completion."""
        ...

    def complete_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        model: Optional[str] = None,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Return structured JSON response validated against schema."""
        ...

    def embed(self, texts: list[str], model: Optional[str] = None) -> list[list[float]]:
        """Return embedding vectors, one per input text."""
        ...

    def is_available(self) -> bool:
        """Health check — allows graceful degradation."""
        ...


# ---------------------------------------------------------------------------
# Graph Store Port (optional — Neo4j)
# ---------------------------------------------------------------------------


@runtime_checkable
class GraphStorePort(Protocol):
    def upsert_document_projection(self, payload: dict[str, Any]) -> None:
        ...

    def upsert_version_projection(self, payload: dict[str, Any]) -> None:
        ...

    def upsert_clause_projection(self, payload: dict[str, Any]) -> None:
        ...

    def upsert_change_record_projection(self, payload: dict[str, Any]) -> None:
        ...

    def run_query(
        self, cypher: str, params: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        ...

    def is_available(self) -> bool:
        ...


# ---------------------------------------------------------------------------
# Document Extractor Port
# ---------------------------------------------------------------------------


@runtime_checkable
class DocumentExtractorPort(Protocol):
    def extract(
        self, file_bytes: bytes, filename: str, options: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Extract raw structured content from document bytes.
        Returns extractor-specific raw output — not the canonical model.
        Callers must pass this output through the canonicalization service.
        """
        ...

    def supports(self, mime_type: str) -> bool:
        ...

    def is_available(self) -> bool:
        ...


# ---------------------------------------------------------------------------
# Embedding Port (separate from LLM to allow dedicated embedding models)
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbeddingPort(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    def is_available(self) -> bool:
        ...
