from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from grc_policy_server.core.celery_app import celery_app
from grc_policy_server.core.config import settings
from grc_policy_server.services.comparison.compare_v2_models import CompareTaskPayload
from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore
from grc_policy_server.services.comparison.comparison_trace import ComparisonTraceStore
from grc_policy_server.services.comparison.real_diff_engine import RealDiffEngine
from grc_policy_server.services.documents.canonical_store import CanonicalDocumentStore
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient, Neo4jSettings
from grc_policy_server.services.llm.base import BaseLLM
from grc_policy_server.services.llm.factory import build_llm
from grc_policy_server.services.vector.weaviate_client import WeaviateClient

logger = logging.getLogger(__name__)


def _build_diff_engine() -> tuple[
    RealDiffEngine,
    WeaviateClient,
    Neo4jClient | None,
    BaseLLM,
]:
    weaviate = WeaviateClient()
    neo4j: Neo4jClient | None = None
    if settings.neo4j_enabled:
        neo4j = Neo4jClient(
            Neo4jSettings(
                uri=settings.neo4j_uri,
                user=settings.neo4j_user,
                password=settings.neo4j_password,
                database=settings.neo4j_database,
            )
        )

    llm = build_llm()
    engine = RealDiffEngine(
        weaviate=weaviate,
        neo4j=neo4j,
        llm=llm,
        canonical_store=CanonicalDocumentStore(
            database_url=settings.database_url,
            upload_root=Path(settings.upload_root),
        ),
        trace_store=ComparisonTraceStore(upload_root=Path(settings.upload_root)),
    )
    return engine, weaviate, neo4j, llm


async def _compare_payload(payload: CompareTaskPayload) -> dict[str, Any]:
    engine, weaviate, neo4j, llm = _build_diff_engine()
    try:
        result = await engine.compare(
            payload.doc1,
            payload.doc2,
            force_re_extract=payload.force_re_extract,
        )
        cache_store = ComparisonCacheStore(upload_root=Path(settings.upload_root))
        cache_store.save_for_key(
            key=payload.cache_key,
            doc1_id=payload.doc1.id,
            doc2_id=payload.doc2.id,
            result=result,
        )
        return {
            "cache_key": payload.cache_key,
            "doc1_id": payload.doc1.id,
            "doc2_id": payload.doc2.id,
            "comparison": result.model_dump(mode="json"),
        }
    finally:
        try:
            weaviate.close()
        except Exception:
            logger.exception("failed to close Weaviate client in compare_v2 task")
        try:
            if neo4j is not None:
                neo4j.close()
        except Exception:
            logger.exception("failed to close Neo4j client in compare_v2 task")
        try:
            await llm.aclose()
        except Exception:
            logger.exception("failed to close LLM client in compare_v2 task")


@celery_app.task(name="grc_policy_server.tasks.compare_v2")
def compare_v2(payload: dict[str, Any]) -> dict[str, Any]:
    parsed = CompareTaskPayload.model_validate(payload)
    return asyncio.run(_compare_payload(parsed))
