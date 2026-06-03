# src/grc_policy_server/api/deps.py
from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Generator
from pathlib import Path

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from grc_policy_server.core.config import settings
from grc_policy_server.core.logging import logging
from grc_policy_server.repositories.documents import DocumentRepository
from grc_policy_server.repositories.human_review import HumanReviewQueue
from grc_policy_server.services.agents.evidence_agent import EvidenceExtractionAgent
from grc_policy_server.services.agents.explanation_agent import ExplanationAgent
from grc_policy_server.services.audit.audit_log import AuditLogStore
from grc_policy_server.services.comparison.compare_v2_dispatcher import (
    CompareV2Dispatcher,
)
from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore
from grc_policy_server.services.comparison.comparison_trace import ComparisonTraceStore
from grc_policy_server.services.comparison.offline_diff_engine import OfflineDiffEngine
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
from grc_policy_server.services.ontology.ontology_classifier import OntologyClassifier
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


def get_audit_log_store() -> AuditLogStore | None:
    """Return an AuditLogStore when AUDIT_LOG_ENABLED=true (default)."""
    if not settings.audit_log_enabled:
        return None
    return AuditLogStore(
        database_url=settings.database_url,
        upload_root=Path(settings.upload_root),
    )


def get_evidence_agent() -> EvidenceExtractionAgent | None:
    """Return an EvidenceExtractionAgent when EVIDENCE_EXTRACTION_ENABLED=true."""
    if not settings.evidence_extraction_enabled:
        return None
    base_url = settings.ollama_url
    if not base_url:
        return None
    return EvidenceExtractionAgent(base_url=base_url, model=settings.ollama_chat_model)


def _should_use_offline_engine() -> bool:
    """Return True when the offline engine should be selected.

    - Always True for COMPARISON_BACKEND=offline.
    - In 'auto' mode: True when Weaviate or LLM health checks report DOWN,
      relying on the circuit-breaking ServiceHealthRegistry.
    - Always False for COMPARISON_BACKEND=online (forces online even if degraded).
    """
    backend = settings.comparison_backend
    if backend == "offline":
        return True
    if backend == "online":
        return False
    # auto mode — probe via registry (reads from cache, never blocks >2s)
    if settings.offline_fallback:
        from grc_policy_server.services.health.service_health_registry import (
            get_service_health_registry,
        )
        registry = get_service_health_registry()
        weaviate_ok = registry.is_healthy("weaviate")
        llm_ok = registry.is_healthy("llm")
        if not weaviate_ok or not llm_ok:
            logger.info(
                "auto mode: degrading to offline engine "
                "(weaviate=%s llm=%s)",
                "up" if weaviate_ok else "down",
                "up" if llm_ok else "down",
            )
            return True
    return False


def get_diff_engine(
    weaviate: WeaviateClient | None = Depends(get_weaviate_client),
    neo4j: Neo4jClient | None = Depends(get_neo4j_client),
    llm: BaseLLM = Depends(get_llm_client),
    canonical_store: CanonicalDocumentStore = Depends(get_canonical_document_store),
    trace_store: ComparisonTraceStore = Depends(get_comparison_trace_store),
    audit_log: AuditLogStore | None = Depends(get_audit_log_store),
    evidence_agent: EvidenceExtractionAgent | None = Depends(get_evidence_agent),
) -> RealDiffEngine:
    if _should_use_offline_engine():
        return OfflineDiffEngine(
            canonical_store=canonical_store,
            trace_store=trace_store,
        )
    return RealDiffEngine(
        weaviate=weaviate,
        neo4j=neo4j,
        llm=llm,
        canonical_store=canonical_store,
        trace_store=trace_store,
        audit_log=audit_log,
        evidence_agent=evidence_agent,
    )


def get_explanation_agent(
    llm: BaseLLM = Depends(get_llm_client),
) -> BaseLLM:
    """Return ExplanationAgent (wrapping the raw LLM) when enabled.

    ExplanationAgent overrides generate_diff_table_row_stream() with a
    focused compliance explanation prompt.  All other BaseLLM methods
    delegate to the underlying Ollama/vLLM client unchanged.
    """
    if settings.explanation_agent_enabled:
        return ExplanationAgent(llm=llm, max_tokens=settings.max_explanation_tokens)
    return llm


def get_diff_engine_stream(
    weaviate: WeaviateClient | None = Depends(get_weaviate_client),
    neo4j: Neo4jClient | None = Depends(get_neo4j_client),
    explanation_llm: BaseLLM = Depends(get_explanation_agent),
    canonical_store: CanonicalDocumentStore = Depends(get_canonical_document_store),
    trace_store: ComparisonTraceStore = Depends(get_comparison_trace_store),
) -> RealDiffEngineStream:
    # Streaming uses the full online engine; offline callers use /compare directly.
    return RealDiffEngineStream(
        weaviate=weaviate,
        neo4j=neo4j,
        llm=explanation_llm,
        canonical_store=canonical_store,
        trace_store=trace_store,
        inter_diff_delay_ms=settings.llm_stream_inter_diff_delay_ms,
    )


def get_document_repository() -> DocumentRepository:
    return DocumentRepository(upload_root=Path(settings.upload_root))


def get_ontology_classifier() -> OntologyClassifier | None:
    """Return an OntologyClassifier instance, or None when disabled."""
    if not settings.ontology_classification_enabled:
        return None
    base_url = settings.ontology_classifier_url or settings.ollama_url
    model = settings.ontology_classifier_model or settings.ollama_chat_model
    if not base_url:
        return None
    return OntologyClassifier(
        base_url=base_url,
        model=model,
        confidence_threshold=settings.ontology_confidence_threshold,
    )


def get_human_review_queue() -> HumanReviewQueue:
    return HumanReviewQueue(
        database_url=settings.database_url,
        upload_root=Path(settings.upload_root),
    )


def get_document_ingestion_service(
    docling_adapter: DoclingAdapter = Depends(get_docling_adapter),
    weaviate: WeaviateClient | None = Depends(get_weaviate_client),
    neo4j: Neo4jClient | None = Depends(get_neo4j_client),
    llm: BaseLLM = Depends(get_llm_client),
    canonical_store: CanonicalDocumentStore = Depends(get_canonical_document_store),
    ontology_classifier: OntologyClassifier | None = Depends(get_ontology_classifier),
    human_review_queue: HumanReviewQueue = Depends(get_human_review_queue),
) -> DocumentIngestionService:
    return DocumentIngestionService(
        docling_adapter=docling_adapter,
        weaviate=weaviate,
        neo4j=neo4j,
        llm=llm,
        upload_root=Path(settings.upload_root),
        canonical_store=canonical_store,
        ontology_classifier=ontology_classifier,
        human_review_queue=human_review_queue,
    )


def get_document_ingestion_service_factory(
    docling_adapter: DoclingAdapter = Depends(get_docling_adapter),
    weaviate: WeaviateClient | None = Depends(get_weaviate_client),
    neo4j: Neo4jClient | None = Depends(get_neo4j_client),
    llm: BaseLLM = Depends(get_llm_client),
    canonical_store: CanonicalDocumentStore = Depends(get_canonical_document_store),
    ontology_classifier: OntologyClassifier | None = Depends(get_ontology_classifier),
    human_review_queue: HumanReviewQueue = Depends(get_human_review_queue),
) -> Callable[[], DocumentIngestionService]:
    def _factory() -> DocumentIngestionService:
        return DocumentIngestionService(
            docling_adapter=docling_adapter,
            weaviate=weaviate,
            neo4j=neo4j,
            llm=llm,
            upload_root=Path(settings.upload_root),
            canonical_store=canonical_store,
            ontology_classifier=ontology_classifier,
            human_review_queue=human_review_queue,
        )

    return _factory


def get_upload_v2_dispatcher() -> UploadV2Dispatcher:
    return UploadV2Dispatcher()


def get_comparison_cache_store() -> ComparisonCacheStore:
    return ComparisonCacheStore(upload_root=Path(settings.upload_root))


def get_compare_v2_dispatcher() -> CompareV2Dispatcher:
    return CompareV2Dispatcher(upload_root=Path(settings.upload_root))
