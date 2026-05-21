# src/grc_policy_server/api/deps.py
from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Generator
from pathlib import Path

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from grc_policy_server.core.config import settings
from grc_policy_server.core.logging import logging
from grc_policy_server.repositories.documents import DocumentRepository
from grc_policy_server.services.comparison.compare_v2_dispatcher import (
    CompareV2Dispatcher,
)
from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore
from grc_policy_server.services.comparison.comparison_trace import ComparisonTraceStore
from grc_policy_server.services.comparison.real_diff_engine import RealDiffEngine
from grc_policy_server.services.comparison.real_diff_engine_stream import (
    RealDiffEngineStream,
)
from grc_policy_server.services.graph.graph_neo4j_client import (
    Neo4jClient,
    Neo4jSettings,
)
from grc_policy_server.services.documents.canonical_store import CanonicalDocumentStore
from grc_policy_server.services.ingestion.docling_adapter import DoclingAdapter
from grc_policy_server.services.ingestion.document_ingestion_service import (
    DocumentIngestionService,
)
from grc_policy_server.services.ingestion.upload_v2_dispatcher import UploadV2Dispatcher
from grc_policy_server.services.llm.base import BaseLLM
from grc_policy_server.services.llm.factory import build_llm
from grc_policy_server.services.llm.ollama_client import OllamaClient, OllamaSettings
from grc_policy_server.services.storage.storage_provider_store import (
    StorageProviderStore,
)
from grc_policy_server.services.vector.weaviate_client import (
    WeaviateClient,
)

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Provide `Bearer <API_BEARER_TOKEN>`.",
)


def require_api_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> None:
    expected_token = settings.api_bearer_token.strip()
    if not expected_token:
        raise RuntimeError("API_BEARER_TOKEN must be configured with a non-empty value.")

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials.credentials != expected_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid bearer token",
        )


def get_weaviate_client() -> Generator[WeaviateClient | None, None, None]:
    _candidate: WeaviateClient | None = None
    try:
        _candidate = WeaviateClient()
        # skip_init_checks=True defers the HTTP meta-endpoint check until the first
        # real operation. Force it now so routes receive None when server is unreachable.
        _candidate.client.connect()
        if not _candidate.client.is_ready():
            raise RuntimeError("Weaviate is not ready")
        try:
            yield _candidate
        finally:
            try:
                _candidate.close()
            except Exception:
                pass
    except Exception:
        logger.warning("Weaviate unavailable — comparison will use local fallback")
        if _candidate is not None:
            try:
                _candidate.close()
            except Exception:
                pass
        yield None


def get_neo4j_client() -> Generator[Neo4jClient | None, None, None]:
    # Neo4j is intentionally disabled by default and can be re-enabled later.
    if not settings.neo4j_enabled:
        yield None
        return

    client = Neo4jClient(
        Neo4jSettings(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password,
            database=settings.neo4j_database,
        )
    )
    try:
        yield client
    finally:
        try:
            client.close()
        except Exception:
            logger.exception("failed to close Neo4j client")


async def get_ollama_client() -> AsyncGenerator[OllamaClient, None]:
    client = OllamaClient(
        OllamaSettings(
            base_url=settings.ollama_url,
            chat_model=settings.ollama_chat_model,
            embed_model=settings.ollama_embed_model,
            connect_timeout_sec=settings.ollama_connect_timeout_sec,
            read_timeout_sec=settings.ollama_timeout_sec,
            write_timeout_sec=settings.ollama_write_timeout_sec,
            opik_enabled=settings.opik_enabled,
            opik_url=settings.opik_url_override,
            opik_project_name=settings.opik_project_name,
            opik_workspace=settings.opik_workspace,
        )
    )
    try:
        yield client
    finally:
        try:
            await client.aclose()
        except Exception:
            logger.exception("failed to close Ollama client")


async def get_llm_client() -> AsyncGenerator[BaseLLM, None]:
    client = build_llm()
    try:
        yield client
    finally:
        try:
            await client.aclose()
        except Exception:
            logger.exception("failed to close LLM client")


def get_docling_adapter() -> DoclingAdapter:
    return DoclingAdapter()


def get_canonical_document_store() -> CanonicalDocumentStore:
    return CanonicalDocumentStore(
        database_url=settings.database_url,
        upload_root=Path(settings.upload_root),
    )


def get_comparison_trace_store() -> ComparisonTraceStore:
    return ComparisonTraceStore(upload_root=Path(settings.upload_root))


def get_storage_provider_store() -> StorageProviderStore:
    return StorageProviderStore(
        database_url=settings.database_url,
        upload_root=Path(settings.upload_root),
    )


def get_diff_engine(
    weaviate: WeaviateClient | None = Depends(get_weaviate_client),
    neo4j: Neo4jClient | None = Depends(get_neo4j_client),
    llm: BaseLLM = Depends(get_llm_client),
    canonical_store: CanonicalDocumentStore = Depends(get_canonical_document_store),
    trace_store: ComparisonTraceStore = Depends(get_comparison_trace_store),
) -> RealDiffEngine:
    return RealDiffEngine(
        weaviate=weaviate,
        neo4j=neo4j,
        llm=llm,
        canonical_store=canonical_store,
        trace_store=trace_store,
    )


def get_diff_engine_stream(
    weaviate: WeaviateClient | None = Depends(get_weaviate_client),
    neo4j: Neo4jClient | None = Depends(get_neo4j_client),
    llm: BaseLLM = Depends(get_llm_client),
    canonical_store: CanonicalDocumentStore = Depends(get_canonical_document_store),
    trace_store: ComparisonTraceStore = Depends(get_comparison_trace_store),
) -> RealDiffEngineStream:
    return RealDiffEngineStream(
        weaviate=weaviate,
        neo4j=neo4j,
        llm=llm,
        canonical_store=canonical_store,
        trace_store=trace_store,
        inter_diff_delay_ms=settings.llm_stream_inter_diff_delay_ms,
    )


def get_document_repository() -> DocumentRepository:
    return DocumentRepository(upload_root=Path(settings.upload_root))


def get_document_ingestion_service(
    docling_adapter: DoclingAdapter = Depends(get_docling_adapter),
    weaviate: WeaviateClient | None = Depends(get_weaviate_client),
    neo4j: Neo4jClient | None = Depends(get_neo4j_client),
    llm: BaseLLM = Depends(get_llm_client),
    canonical_store: CanonicalDocumentStore = Depends(get_canonical_document_store),
) -> DocumentIngestionService:
    return DocumentIngestionService(
        docling_adapter=docling_adapter,
        weaviate=weaviate,
        neo4j=neo4j,
        llm=llm,
        upload_root=Path(settings.upload_root),
        canonical_store=canonical_store,
    )


def get_document_ingestion_service_factory(
    docling_adapter: DoclingAdapter = Depends(get_docling_adapter),
    weaviate: WeaviateClient | None = Depends(get_weaviate_client),
    neo4j: Neo4jClient | None = Depends(get_neo4j_client),
    llm: BaseLLM = Depends(get_llm_client),
    canonical_store: CanonicalDocumentStore = Depends(get_canonical_document_store),
) -> Callable[[], DocumentIngestionService]:
    def _factory() -> DocumentIngestionService:
        return DocumentIngestionService(
            docling_adapter=docling_adapter,
            weaviate=weaviate,
            neo4j=neo4j,
            llm=llm,
            upload_root=Path(settings.upload_root),
            canonical_store=canonical_store,
        )

    return _factory


def get_upload_v2_dispatcher() -> UploadV2Dispatcher:
    return UploadV2Dispatcher()


def get_comparison_cache_store() -> ComparisonCacheStore:
    return ComparisonCacheStore(upload_root=Path(settings.upload_root))


def get_compare_v2_dispatcher() -> CompareV2Dispatcher:
    return CompareV2Dispatcher(upload_root=Path(settings.upload_root))
