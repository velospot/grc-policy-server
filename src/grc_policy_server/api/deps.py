# src/grc_policy_server/api/deps.py
from __future__ import annotations

import os

from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine
from grc_policy_server.services.comparision.real_diff_engine_stream import (
    RealDiffEngineStream,
)
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
