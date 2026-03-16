from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from grc_policy_server.api.deps import (
    get_compare_v2_dispatcher,
    get_comparison_cache_store,
    require_api_bearer_token,
)
from grc_policy_server.models.schemas import (
    CompareRequest,
    CompareV2JobCreateResponse,
    CompareV2JobStatusResponse,
)
from grc_policy_server.services.comparision.compare_v2_dispatcher import (
    CeleryNotAvailableError,
    CeleryTaskFailureError,
    CeleryWorkerUnavailableError,
    CompareV2Dispatcher,
)
from grc_policy_server.services.comparision.compare_v2_models import CompareTaskPayload
from grc_policy_server.services.comparision.comparison_cache import ComparisonCacheStore


def _celery_exc_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, (CeleryNotAvailableError, CeleryWorkerUnavailableError)):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

router = APIRouter(
    prefix="/v2/compare",
    tags=["compare"],
    dependencies=[Depends(require_api_bearer_token)],
)


@router.post(
    "",
    response_model=CompareV2JobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue comparison job (Celery worker)",
)
def compare_v2_enqueue(
    payload: CompareRequest,
    dispatcher: CompareV2Dispatcher = Depends(get_compare_v2_dispatcher),
    cache_store: ComparisonCacheStore = Depends(get_comparison_cache_store),
):
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

    if not payload.forceReExtract:
        cached_result = cache_store.load_for_pair(doc1_id=doc1_id, doc2_id=doc2_id)
        if cached_result is not None:
            return CompareV2JobCreateResponse(
                jobId=cache_store.cached_job_id_for_pair(
                    doc1_id=doc1_id,
                    doc2_id=doc2_id,
                ),
                status="finished",
                cacheHit=True,
                result=cached_result,
            )

    cache_key = cache_store.cache_key_for_pair(doc1_id=doc1_id, doc2_id=doc2_id)
    task_payload = CompareTaskPayload(
        doc1=payload.doc1,
        doc2=payload.doc2,
        force_re_extract=payload.forceReExtract,
        cache_key=cache_key,
    )

    try:
        job_id = dispatcher.enqueue_compare(task_payload)
        return CompareV2JobCreateResponse(
            jobId=job_id,
            status="queued",
            cacheHit=False,
        )
    except (CeleryNotAvailableError, CeleryWorkerUnavailableError, CeleryTaskFailureError) as exc:
        raise _celery_exc_to_http(exc) from exc


@router.get(
    "/response/{job_id}",
    response_model=CompareV2JobStatusResponse,
    summary="Poll compare v2 job status/result",
)
def compare_v2_response(
    job_id: str,
    dispatcher: CompareV2Dispatcher = Depends(get_compare_v2_dispatcher),
):
    resolved_job_id = job_id.strip()
    if not resolved_job_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="job_id must not be empty",
        )

    try:
        return dispatcher.get_compare_status(job_id=resolved_job_id)
    except (CeleryNotAvailableError, CeleryWorkerUnavailableError, CeleryTaskFailureError) as exc:
        raise _celery_exc_to_http(exc) from exc


@router.get(
    "/response",
    response_model=CompareV2JobStatusResponse,
    summary="Poll compare v2 job status/result",
)
def compare_v2_response_query(
    job_id: str | None = Query(default=None),
    jobid: str | None = Query(default=None),
    dispatcher: CompareV2Dispatcher = Depends(get_compare_v2_dispatcher),
):
    resolved_job_id = (job_id or jobid or "").strip()
    if not resolved_job_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="job_id must not be empty",
        )

    try:
        return dispatcher.get_compare_status(job_id=resolved_job_id)
    except (CeleryNotAvailableError, CeleryWorkerUnavailableError, CeleryTaskFailureError) as exc:
        raise _celery_exc_to_http(exc) from exc
