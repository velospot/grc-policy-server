from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from billiard.exceptions import SoftTimeLimitExceeded

from grc_policy_server.core.celery_app import celery_app
from grc_policy_server.core.config import settings
from grc_policy_server.services.comparison.auditor_v5 import (
    infer_testing_department,
    shape_v5_auditor_result,
)
from grc_policy_server.services.comparison.compare_v2_models import CompareTaskPayload
from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore
from grc_policy_server.services.comparison.comparison_trace import ComparisonTraceStore
from grc_policy_server.services.comparison.real_diff_engine import RealDiffEngine
from grc_policy_server.services.documents.canonical_store import CanonicalDocumentStore
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient, Neo4jSettings
from grc_policy_server.services.llm.base import BaseLLM
from grc_policy_server.services.llm.factory import build_llm
from grc_policy_server.services.vector.qdrant_store import QdrantVectorClient

logger = logging.getLogger(__name__)


def _build_diff_engine(
    *,
    api_version: str = "v2",
) -> tuple[
    RealDiffEngine,
    QdrantVectorClient | None,
    Neo4jClient | None,
    BaseLLM,
]:
    qdrant: QdrantVectorClient | None = None
    try:
        qdrant = QdrantVectorClient()
        # Probe — fails immediately if Qdrant is unreachable.
        qdrant._client.get_collections()
    except Exception:
        logger.warning("Qdrant unavailable in compare task — local fallback will be used")
        if qdrant is not None:
            try:
                qdrant.close()
            except Exception:
                pass
        qdrant = None
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
        qdrant=qdrant,
        neo4j=neo4j,
        llm=llm,
        canonical_store=CanonicalDocumentStore(
            database_url=settings.database_url,
            upload_root=Path(settings.upload_root),
        ),
        trace_store=ComparisonTraceStore(upload_root=Path(settings.upload_root)),
        max_diffs=1000 if api_version == "v5" else 40,
        max_llm_explanations=40,
        max_llm_markdown_summaries=40,
        suppress_low_diffs=api_version != "v5",
    )
    return engine, qdrant, neo4j, llm


async def _run_compare(
    payload: CompareTaskPayload,
    engine: RealDiffEngine,
    qdrant: QdrantVectorClient | None,
    neo4j: Neo4jClient | None,
    llm: BaseLLM,
) -> dict[str, Any]:
    """Run the actual comparison and save results."""
    try:
        effective_save_to_db = payload.save_to_db or settings.save_comparison_to_db
        testing_department = payload.testing_department or infer_testing_department(
            payload.doc1,
            payload.doc2,
        )
        result = await engine.compare(
            payload.doc1,
            payload.doc2,
            force_re_extract=payload.force_re_extract,
            audit_mode=True if payload.api_version == "v5" else payload.audit_mode,
            save_to_db=effective_save_to_db and payload.api_version != "v5",
            testing_department=testing_department,
        )
        if payload.api_version == "v5":
            result = shape_v5_auditor_result(
                result,
                doc1=payload.doc1,
                doc2=payload.doc2,
                testing_department=testing_department,
            )
            if effective_save_to_db:
                engine._try_save_comparison_to_postgres(  # noqa: SLF001
                    payload.doc1.id,
                    payload.doc2.id,
                    result,
                    True,
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
            if qdrant is not None:
                qdrant.close()
        except Exception:
            logger.exception("failed to close Qdrant client in compare_v2 task")
        try:
            if neo4j is not None:
                neo4j.close()
        except Exception:
            logger.exception("failed to close Neo4j client in compare_v2 task")
        try:
            await llm.aclose()
        except Exception:
            logger.exception("failed to close LLM client in compare_v2 task")


async def _compare_payload(payload: CompareTaskPayload) -> dict[str, Any]:
    """Wrap comparison with timeout budget and service initialization."""
    engine, qdrant, neo4j, llm = _build_diff_engine(api_version=payload.api_version)
    # Budget: soft_time_limit minus 120s for startup + result serialisation.
    async_budget = max(60.0, settings.celery_task_soft_time_limit_sec - 120.0)
    try:
        return await asyncio.wait_for(
            _run_compare(payload, engine, qdrant, neo4j, llm),
            timeout=async_budget,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "compare_v2 async budget exhausted doc1=%s doc2=%s budget=%.0fs",
            payload.doc1.id,
            payload.doc2.id,
            async_budget,
        )
        return {
            "status": "timeout",
            "error": f"Comparison exceeded internal async budget of {async_budget:.0f}s.",
            "doc1Id": payload.doc1.id,
            "doc2Id": payload.doc2.id,
        }


@celery_app.task(name="grc_policy_server.tasks.compare_v2")
def compare_v2(payload: dict[str, Any]) -> dict[str, Any]:
    parsed = CompareTaskPayload.model_validate(payload)
    try:
        return asyncio.run(_compare_payload(parsed))
    except SoftTimeLimitExceeded:
        logger.warning(
            "compare_v2 soft time limit exceeded task_id=%s doc1=%s doc2=%s",
            compare_v2.request.id,
            parsed.doc1.id,
            parsed.doc2.id,
        )
        return {
            "status": "timeout",
            "error": "Comparison exceeded the time limit. Partial results may be available.",
            "doc1Id": parsed.doc1.id,
            "doc2Id": parsed.doc2.id,
        }
