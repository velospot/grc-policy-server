from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from grc_policy_server.api.deps import (
    get_compare_v5_dispatcher,
    get_compare_v5_stream_service,
    get_comparison_cache_store,
    require_api_bearer_token,
)
from grc_policy_server.models.schemas import (
    CompareRequest,
    CompareStreamV5Request,
    CompareV2JobCreateResponse,
)
from grc_policy_server.api.routes.compare_v2 import enqueue_compare_request
from grc_policy_server.services.comparison.compare_v2_dispatcher import (
    CompareV2Dispatcher,
)
from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore
from grc_policy_server.services.comparison.compare_v5_service import (
    CompareV5DocumentNotFoundError,
    CompareV5Service,
    CompareV5ValidationError,
)

router = APIRouter(
    prefix="/v5",
    tags=["compare-v5"],
    dependencies=[Depends(require_api_bearer_token)],
)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _infer_testing_department(payload: CompareRequest) -> str:
    candidates = [
        payload.doc1.category,
        payload.doc2.category,
        payload.doc1.name,
        payload.doc2.name,
    ]
    for value in candidates:
        normalized = str(value or "").strip().lower()
        if normalized in {"emc", "emv"}:
            return "EMC"
        if normalized == "safety":
            return "Safety"
        if normalized in {"environment", "environmental"}:
            return "Environment"
    joined = " ".join(str(value or "").lower() for value in candidates)
    if any(token in joined for token in ("emc", "emv", "cispr", "emission")):
        return "EMC"
    if any(token in joined for token in ("safety", "iec", "hazard")):
        return "Safety"
    if any(token in joined for token in ("rohs", "reach", "environment")):
        return "Environment"
    return "EMC"


def _service_exc_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, CompareV5ValidationError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, CompareV5DocumentNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/compare",
    response_model=CompareV2JobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue v5 hybrid comparison job (Celery worker)",
)
def compare_v5(
    payload: CompareRequest,
    dispatcher: CompareV2Dispatcher = Depends(get_compare_v5_dispatcher),
    cache_store: ComparisonCacheStore = Depends(get_comparison_cache_store),
) -> CompareV2JobCreateResponse:
    return enqueue_compare_request(
        payload,
        dispatcher=dispatcher,
        cache_store=cache_store,
        api_version="v5",
        testing_department=_infer_testing_department(payload),
    )


@router.post(
    "/compare/stream",
    summary="Stream v5 hybrid comparison results as Server-Sent Events",
)
async def compare_v5_stream(
    payload: CompareStreamV5Request,
    service: CompareV5Service = Depends(get_compare_v5_stream_service),
) -> StreamingResponse:
    """Confidence-aware v5 stream (extends the v4 two-stage contract).

    | Event | Key fields | Notes |
    |---|---|---|
    | `payload` | `doc1_id`, `doc2_id`, `testing_department` | First event |
    | `extraction_quality` | `document_id`, `avg_extraction_confidence`, `low_confidence_nodes`, `ocr_nodes`, `ingestion_confidence_metrics`, `docling_mean_grade`, `docling_low_grade`, `docling_scores` | One per document; docling fields from the native ConfidenceReport |
    | `progress` | `stage`, `message`, `total` | Pipeline stages |
    | `diff_start` | v4 fields + `extraction_confidence`, `review_reasons` | Per diff |
    | `diff_token` | `change_id`, `token` | Only for LLM-analysed diffs |
    | `diff_complete` | v4 fields + `extraction_confidence`, `review_reasons`, `analysis_source` (`llm`/`deterministic`) | Per diff |
    | `table_complete` / `summary_*` | as v4 | |
    | `done` | v4 fields + `extraction_review_count`, `semantic_review_count`, `llm_calls`, `extraction_quality` | Final event |

    Low-impact diffs and diffs whose evidence extraction confidence is below
    `extraction_review_threshold` are analysed deterministically (no LLM call);
    low-confidence diffs are also enqueued to the human review queue.
    """
    try:
        events = service.stream_events(payload)
    except (
        CompareV5ValidationError,
        CompareV5DocumentNotFoundError,
    ) as exc:
        raise _service_exc_to_http(exc) from exc

    async def event_generator():
        try:
            async for event in events:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
