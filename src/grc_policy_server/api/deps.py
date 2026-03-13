# src/grc_policy_server/api/deps.py
from __future__ import annotations

from collections.abc import Callable, Generator
from pathlib import Path

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from grc_policy_server.core.config import settings
from grc_policy_server.core.logging import logging
from grc_policy_server.respositories.documents import DocumentRepository
from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine
from grc_policy_server.services.comparision.real_diff_engine_stream import (
    RealDiffEngineStream,
)
from grc_policy_server.services.graph.graph_neo4j_client import (
    Neo4jClient,
    Neo4jSettings,
)
from grc_policy_server.services.ingestion.docling_adapter import DoclingAdapter
from grc_policy_server.services.ingestion.document_ingestion_service import (
    DocumentIngestionService,
)
from grc_policy_server.services.ingestion.upload_v2_dispatcher import UploadV2Dispatcher
from grc_policy_server.services.llm.ollama_client import OllamaClient, OllamaSettings
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
        )

    if credentials.credentials != expected_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid bearer token",
        )


def get_weaviate_client() -> Generator[WeaviateClient, None, None]:
    client = WeaviateClient()
    try:
        yield client
    finally:
        try:
            client.close()
        except Exception:
            logger.exception("failed to close Weaviate client")


def get_neo4j_client() -> Generator[Neo4jClient, None, None]:
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


def get_ollama_client() -> Generator[OllamaClient, None, None]:
    client = OllamaClient(
        OllamaSettings(
            base_url=settings.ollama_url,
            chat_model=settings.ollama_chat_model,
            embed_model=settings.ollama_embed_model,
            timeout_sec=settings.ollama_timeout_sec,
        )
    )
    try:
        yield client
    finally:
        try:
            client.close()
        except Exception:
            logger.exception("failed to close Ollama client")


def get_docling_adapter() -> DoclingAdapter:
    return DoclingAdapter()


def get_diff_engine(
    weaviate: WeaviateClient = Depends(get_weaviate_client),
    neo4j: Neo4jClient = Depends(get_neo4j_client),
    llm: OllamaClient = Depends(get_ollama_client),
) -> RealDiffEngine:
    return RealDiffEngine(
        weaviate=weaviate,
        neo4j=neo4j,
        llm=llm,
    )


def get_diff_engine_stream(
    weaviate: WeaviateClient = Depends(get_weaviate_client),
    neo4j: Neo4jClient = Depends(get_neo4j_client),
    llm: OllamaClient = Depends(get_ollama_client),
) -> RealDiffEngineStream:
    return RealDiffEngineStream(
        weaviate=weaviate,
        neo4j=neo4j,
        llm=llm,
    )


def get_document_repository() -> DocumentRepository:
    return DocumentRepository(upload_root=Path(settings.upload_root))


def get_document_ingestion_service(
    docling_adapter: DoclingAdapter = Depends(get_docling_adapter),
    weaviate: WeaviateClient = Depends(get_weaviate_client),
    neo4j: Neo4jClient = Depends(get_neo4j_client),
    llm: OllamaClient = Depends(get_ollama_client),
) -> DocumentIngestionService:
    return DocumentIngestionService(
        docling_adapter=docling_adapter,
        weaviate=weaviate,
        neo4j=neo4j,
        llm=llm,
        upload_root=Path(settings.upload_root),
    )


def get_document_ingestion_service_factory(
    docling_adapter: DoclingAdapter = Depends(get_docling_adapter),
    weaviate: WeaviateClient = Depends(get_weaviate_client),
    neo4j: Neo4jClient = Depends(get_neo4j_client),
    llm: OllamaClient = Depends(get_ollama_client),
) -> Callable[[], DocumentIngestionService]:
    def _factory() -> DocumentIngestionService:
        return DocumentIngestionService(
            docling_adapter=docling_adapter,
            weaviate=weaviate,
            neo4j=neo4j,
            llm=llm,
            upload_root=Path(settings.upload_root),
        )

    return _factory


def get_upload_v2_dispatcher() -> UploadV2Dispatcher:
    return UploadV2Dispatcher()
