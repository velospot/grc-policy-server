# src/grc_policy_server/api/deps.py
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from grc_policy_server.core.config import settings
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
from grc_policy_server.services.llm.ollama_client import OllamaClient, OllamaSettings
from grc_policy_server.services.vector.weaviate_client import (
    WeaviateClient,
)


def get_weaviate_client() -> WeaviateClient:
    return WeaviateClient()


def get_neo4j_client() -> Neo4jClient:
    return Neo4jClient(
        Neo4jSettings(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password,
            database=settings.neo4j_database,
        )
    )


def get_ollama_client() -> OllamaClient:
    return OllamaClient(
        OllamaSettings(
            base_url=settings.ollama_url,
            chat_model=settings.ollama_chat_model,
            embed_model=settings.ollama_embed_model,
        )
    )


def get_docling_adapter() -> DoclingAdapter:
    return DoclingAdapter()


def get_diff_engine() -> RealDiffEngine:
    return RealDiffEngine(
        weaviate=get_weaviate_client(),
        neo4j=get_neo4j_client(),
        llm=get_ollama_client(),
    )


def get_diff_engine_stream() -> RealDiffEngineStream:
    return RealDiffEngineStream(
        weaviate=get_weaviate_client(),
        neo4j=get_neo4j_client(),
        llm=get_ollama_client(),
    )


def get_document_repository() -> DocumentRepository:
    return DocumentRepository(upload_root=Path(settings.upload_root))


def get_document_ingestion_service() -> DocumentIngestionService:
    return DocumentIngestionService(
        docling_adapter=get_docling_adapter(),
        weaviate=get_weaviate_client(),
        neo4j=get_neo4j_client(),
        llm=get_ollama_client(),
        upload_root=Path(settings.upload_root),
    )


def get_document_ingestion_service_factory() -> Callable[[], DocumentIngestionService]:
    return get_document_ingestion_service
