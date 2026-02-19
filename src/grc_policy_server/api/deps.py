# src/grc_policy_server/api/deps.py
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from grc_policy_server.core.config import settings
from grc_policy_server.services.ingestion.docling_adapter import DoclingAdapter
from grc_policy_server.services.ingestion.document_ingestion_service import (
    DocumentIngestionService,
)
from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine
from grc_policy_server.services.comparision.real_diff_engine_stream import (
    RealDiffEngineStream,
)
from grc_policy_server.respositories.documents import DocumentRepository
from grc_policy_server.services.graph.graph_neo4j_client import (
    Neo4jClient,
    Neo4jSettings,
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
            uri=os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
            user=os.getenv("NEO4J_USER", "neo4j"),
            password=os.getenv("NEO4J_PASSWORD", "password"),
            database=os.getenv("NEO4J_DATABASE", "neo4j"),
        )
    )


def get_ollama_client() -> OllamaClient:
    return OllamaClient(
        OllamaSettings(
            base_url=os.getenv("OLLAMA_URL", "http://ollama:11434"),
            chat_model=os.getenv("OLLAMA_CHAT_MODEL", "llama3.1"),
            embed_model=os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
            request_timeout_sec=float(os.getenv("OLLAMA_TIMEOUT_SEC", "180")),
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
    return DocumentRepository()


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
