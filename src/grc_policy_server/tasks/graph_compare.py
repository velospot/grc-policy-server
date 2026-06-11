from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from grc_policy_server.core.celery_app import celery_app
from grc_policy_server.core.config import settings
from grc_policy_server.models.schemas import GraphCompareTaskPayload
from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore
from grc_policy_server.services.comparison.graph_tree_compare import (
    GraphArtifactStore,
    GraphTreeComparisonOrchestrator,
)

logger = logging.getLogger(__name__)


@celery_app.task(name="grc_policy_server.tasks.graph_compare", bind=False)
def graph_compare(payload: dict[str, Any]) -> dict[str, Any]:
    """Celery task: run graph-tree comparison and return ComparisonResult dict.

    Return shape is identical to grc_policy_server.tasks.compare_v2 so that
    CompareV2Dispatcher.get_compare_status() resolves graph-compare job IDs
    transparently via GET /v2/compare/response/{jobId}.
    """
    task_payload = GraphCompareTaskPayload.model_validate(payload)
    upload_root = Path(settings.upload_root)

    artifact_store = GraphArtifactStore(upload_root=upload_root)

    explanation_agent = None
    if settings.explanation_agent_enabled:
        try:
            from grc_policy_server.services.agents.graph_explanation_agent import GraphExplanationAgent
            from grc_policy_server.services.llm.factory import build_llm
            explanation_agent = GraphExplanationAgent(
                llm=build_llm(),
                timeout_sec=settings.graph_explanation_timeout_sec,
            )
        except Exception:
            logger.warning(
                "graph_compare: could not init GraphExplanationAgent — markdownDiffSummary will be null",
                exc_info=True,
            )

    orchestrator = GraphTreeComparisonOrchestrator(
        artifact_store=artifact_store,
        explanation_agent=explanation_agent,
    )

    try:
        result = asyncio.run(
            orchestrator.compare_as_current_response(
                doc1_id=task_payload.doc1.id,
                doc2_id=task_payload.doc2.id,
                testing_department=task_payload.testing_department,
                include_unchanged=task_payload.include_unchanged,
            )
        )
    except Exception:
        logger.exception(
            "graph_compare task failed doc1=%s doc2=%s",
            task_payload.doc1.id,
            task_payload.doc2.id,
        )
        raise

    cache_store = ComparisonCacheStore(upload_root=upload_root)
    try:
        cache_store.save_for_key(
            key=task_payload.cache_key,
            doc1_id=task_payload.doc1.id,
            doc2_id=task_payload.doc2.id,
            result=result,
        )
    except Exception:
        logger.warning("graph_compare: failed to write cache — result still returned", exc_info=True)

    return {
        "cache_key": task_payload.cache_key,
        "doc1_id": task_payload.doc1.id,
        "doc2_id": task_payload.doc2.id,
        "comparison": result.model_dump(mode="json"),
    }
