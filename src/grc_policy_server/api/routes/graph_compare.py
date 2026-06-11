from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from grc_policy_server.api.deps import (
    get_compare_v2_dispatcher,
    get_graph_tree_comparison_orchestrator,
    require_api_bearer_token,
)
from grc_policy_server.core.config import settings
from grc_policy_server.models.schemas import (
    CompareV2JobCreateResponse,
    GraphChangeRecord,
    GraphCompareRequest,
    GraphCompareTaskPayload,
    GraphComparisonResult,
)
from grc_policy_server.services.comparison.compare_v2_dispatcher import CompareV2Dispatcher
from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore
from grc_policy_server.services.comparison.graph_tree_compare import (
    GraphTreeComparisonOrchestrator,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/graph-compare",
    tags=["graph-compare"],
    dependencies=[Depends(require_api_bearer_token)],
)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _validate_payload(payload: GraphCompareRequest) -> tuple[str, str]:
    doc1_id = payload.doc1.id.strip()
    doc2_id = payload.doc2.id.strip()
    if not doc1_id or not doc2_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="doc1.id and doc2.id must not be empty",
        )
    if doc1_id == doc2_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="doc1.id and doc2.id must be different",
        )
    return doc1_id, doc2_id


def _enqueue_graph_compare(
    task_payload: GraphCompareTaskPayload,
    dispatcher: CompareV2Dispatcher,
) -> str:
    """Queue the graph-compare task via Celery; fall back to synchronous offline run."""
    from grc_policy_server.core.celery_app import celery_app

    try:
        async_result = celery_app.send_task(
            "grc_policy_server.tasks.graph_compare",
            args=[task_payload.model_dump(mode="json")],
            queue=settings.celery_default_queue,
        )
        return str(async_result.id)
    except Exception:
        logger.warning(
            "Celery unavailable for graph-compare — running synchronously",
            exc_info=True,
        )

    # Offline fallback: run synchronously, save to cache, return "cached-" job ID
    import asyncio as _asyncio
    from grc_policy_server.services.comparison.graph_tree_compare import (
        GraphArtifactStore,
        GraphTreeComparisonOrchestrator,
    )
    upload_root = Path(settings.upload_root)
    orchestrator = GraphTreeComparisonOrchestrator(
        artifact_store=GraphArtifactStore(upload_root=upload_root)
    )
    result = _asyncio.run(
        orchestrator.compare_as_current_response(
            doc1_id=task_payload.doc1.id,
            doc2_id=task_payload.doc2.id,
            testing_department=task_payload.testing_department,
            include_unchanged=task_payload.include_unchanged,
        )
    )
    cache_store = ComparisonCacheStore(upload_root=upload_root)
    cache_store.save_for_key(
        key=task_payload.cache_key,
        doc1_id=task_payload.doc1.id,
        doc2_id=task_payload.doc2.id,
        result=result,
    )
    return cache_store.cached_job_id_for_pair(doc1_id=task_payload.doc1.id, doc2_id=task_payload.doc2.id)


@router.post(
    "",
    response_model=CompareV2JobCreateResponse,
    summary="Queue a graph-tree comparison and return a job ID",
    description=(
        "Enqueues a graph-first compliance comparison as a Celery background task. "
        "Poll GET /v2/compare/response/{jobId} for results. "
        "Returns immediately with status='finished' on cache hit."
    ),
)
async def compare_document_graphs(
    payload: GraphCompareRequest,
    dispatcher: CompareV2Dispatcher = Depends(get_compare_v2_dispatcher),
) -> CompareV2JobCreateResponse:
    doc1_id, doc2_id = _validate_payload(payload)
    upload_root = Path(settings.upload_root)
    cache_store = ComparisonCacheStore(upload_root=upload_root)

    if not payload.forceReExtract:
        cached = cache_store.load_for_pair(doc1_id=doc1_id, doc2_id=doc2_id)
        if cached is not None:
            job_id = cache_store.cached_job_id_for_pair(doc1_id=doc1_id, doc2_id=doc2_id)
            return CompareV2JobCreateResponse(
                jobId=job_id,
                status="finished",
                cacheHit=True,
                result=cached,
            )

    cache_key = cache_store.cache_key_for_pair(doc1_id=doc1_id, doc2_id=doc2_id)
    task_payload = GraphCompareTaskPayload(
        doc1=payload.doc1,
        doc2=payload.doc2,
        testing_department=payload.testingDepartment or "",
        include_unchanged=payload.includeUnchanged,
        audit_mode=payload.auditMode,
        force_re_extract=payload.forceReExtract,
        cache_key=cache_key,
    )

    try:
        job_id = _enqueue_graph_compare(task_payload, dispatcher)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return CompareV2JobCreateResponse(jobId=job_id, status="queued", cacheHit=False)


@router.post(
    "/stream",
    summary="Stream document graph-tree comparison as Server-Sent Events",
    description=(
        "Streams graph-first comparison progress and per-change records in real time. "
        "Event types: payload, progress, diff, done, error. "
        "For async polling use POST /graph-compare + GET /v2/compare/response/{jobId}."
    ),
)
async def compare_document_graphs_stream(
    payload: GraphCompareRequest,
    orchestrator: GraphTreeComparisonOrchestrator = Depends(
        get_graph_tree_comparison_orchestrator
    ),
) -> StreamingResponse:
    doc1_id, doc2_id = _validate_payload(payload)

    async def event_generator():
        try:
            async for event in orchestrator.compare_events(
                doc1_id=doc1_id,
                doc2_id=doc2_id,
                testing_department=payload.testingDepartment or "",
                include_unchanged=payload.includeUnchanged,
            ):
                if event.get("type") == "change":
                    if event.get("key_difference"):
                        diff_payload = event["key_difference"]
                    else:
                        diff = await orchestrator.key_difference_for_change(
                            GraphChangeRecord.model_validate(event["change"]),
                            testing_department=payload.testingDepartment or "",
                        )
                        diff_payload = diff.model_dump(mode="json")
                    event = {"type": "diff", "item": diff_payload}
                elif event.get("type") == "done":
                    result = await orchestrator.to_current_response(
                        GraphComparisonResult.model_validate(event["result"]),
                        testing_department=payload.testingDepartment or "",
                    )
                    event = {
                        "type": "done",
                        "summary": result.summary,
                        "keyDifferences": [
                            item.model_dump(mode="json") for item in result.keyDifferences
                        ],
                        "actionPlan": [
                            item.model_dump(mode="json") for item in result.actionPlan
                        ],
                        "followUpQuestions": result.followUpQuestions,
                        "accuracyMetrics": (
                            result.accuracyMetrics.model_dump(mode="json")
                            if result.accuracyMetrics
                            else None
                        ),
                        "requireHumanReview": result.requireHumanReview,
                        "warnings": result.warnings,
                    }
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield (
                "data: "
                f"{json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)}"
                "\n\n"
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
