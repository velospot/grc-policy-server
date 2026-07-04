from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from grc_policy_server.models.schemas import (
    CompareStreamV5Request,
    Document,
)
from grc_policy_server.repositories.documents import DocumentRepository
from grc_policy_server.services.comparison.real_diff_engine_stream import (
    RealDiffEngineStream,
)
from grc_policy_server.services.documents.mapper import to_document_response


class CompareV5ValidationError(ValueError):
    pass


class CompareV5DocumentNotFoundError(LookupError):
    pass


@dataclass
class CompareV5Service:
    """Versioned stream comparison service for `/v5/compare/stream`.

    Synchronous `/v5/compare` intentionally reuses the v2 queued-job contract.
    Streaming uses the confidence-aware `compare_stream_v5` engine contract:
    per-document `extraction_quality` events, per-diff extraction confidence and
    review reasons, confidence/severity-gated LLM usage, and human-review-queue
    enqueueing for low-confidence evidence.
    """

    document_repo: DocumentRepository
    stream_engine: RealDiffEngineStream | None = None

    def stream_events(
        self,
        payload: CompareStreamV5Request,
    ) -> AsyncIterator[dict[str, Any]]:
        doc1, doc2 = self._load_documents(payload.doc1Id, payload.doc2Id)
        if self.stream_engine is None:
            raise RuntimeError("CompareV5Service requires stream_engine for stream")

        async def _events() -> AsyncIterator[dict[str, Any]]:
            if self.stream_engine is None:
                raise RuntimeError("CompareV5Service requires stream_engine for stream")
            async for event in self.stream_engine.compare_stream_v5(
                doc1,
                doc2,
                force_re_extract=payload.forceReExtract,
                testing_department=payload.testingDepartment,
            ):
                yield self._decorate_stream_event(
                    event,
                    doc1=doc1,
                    doc2=doc2,
                    payload=payload,
                )

        return _events()

    def _load_documents(self, doc1_id: str, doc2_id: str) -> tuple[Document, Document]:
        resolved_doc1_id, resolved_doc2_id = self._validate_doc_ids(doc1_id, doc2_id)
        doc1 = self._load_document(resolved_doc1_id)
        doc2 = self._load_document(resolved_doc2_id)
        if doc1 is None or doc2 is None:
            missing = []
            if doc1 is None:
                missing.append("doc1Id")
            if doc2 is None:
                missing.append("doc2Id")
            raise CompareV5DocumentNotFoundError(
                "Document metadata or canonical comparison nodes not found for: "
                f"{', '.join(missing)}"
            )
        return doc1, doc2

    def _load_document(self, document_id: str) -> Document | None:
        doc_domain = self.document_repo.get_document(document_id)
        if doc_domain is not None:
            return to_document_response(doc_domain)
        if not self._has_canonical_comparison_nodes(document_id):
            return None
        return Document(
            id=document_id,
            name=document_id,
            version="unknown",
            uploadDate="unknown",
            size="unknown",
            category="canonical",
        )

    def _has_canonical_comparison_nodes(self, document_id: str) -> bool:
        canonical_store = getattr(self.stream_engine, "canonical_store", None)
        loader = getattr(canonical_store, "load_comparison_nodes", None)
        if loader is None:
            return False
        try:
            return bool(loader(document_id))
        except Exception:
            return False

    @staticmethod
    def _validate_doc_ids(doc1_id: str, doc2_id: str) -> tuple[str, str]:
        resolved_doc1_id = doc1_id.strip()
        resolved_doc2_id = doc2_id.strip()
        if not resolved_doc1_id or not resolved_doc2_id:
            raise CompareV5ValidationError("doc1Id and doc2Id must not be empty")
        if resolved_doc1_id == resolved_doc2_id:
            raise CompareV5ValidationError("doc1Id and doc2Id must be different")
        return resolved_doc1_id, resolved_doc2_id

    def _hybrid_signals(self) -> dict[str, str | bool | None]:
        return {
            "qdrantAvailable": getattr(self.stream_engine, "qdrant", None) is not None,
            "neo4jAvailable": getattr(self.stream_engine, "neo4j", None) is not None,
            "canonicalStoreAvailable": getattr(
                self.stream_engine,
                "canonical_store",
                None,
            )
            is not None,
            "matchingStrategy": "canonical+qdrant+neo4j_graph_fallback",
        }

    def _decorate_stream_event(
        self,
        event: dict[str, Any],
        *,
        doc1: Document,
        doc2: Document,
        payload: CompareStreamV5Request,
    ) -> dict[str, Any]:
        decorated = dict(event)
        decorated.setdefault("apiVersion", "v5")
        decorated.setdefault("serviceVersion", "compare-v5")
        decorated.setdefault("responseSchema", "comparison_stream_event_v5")
        decorated.setdefault("doc1Id", doc1.id)
        decorated.setdefault("doc2Id", doc2.id)
        decorated.setdefault("testingDepartment", payload.testingDepartment)
        if decorated.get("type") == "done":
            decorated.setdefault("hybridSignals", self._hybrid_signals())
        return decorated
