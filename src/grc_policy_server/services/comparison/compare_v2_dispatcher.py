from __future__ import annotations

from pathlib import Path

from grc_policy_server.core.config import settings
from grc_policy_server.models.schemas import ComparisonResult, CompareV2JobStatusResponse
from grc_policy_server.services.comparison.compare_v2_models import CompareTaskPayload
from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore


class CeleryNotAvailableError(RuntimeError):
    """Raised when Celery is not installed in the runtime."""


class CeleryWorkerUnavailableError(RuntimeError):
    """Raised when no Celery worker is available to process the task."""


class CeleryTaskFailureError(RuntimeError):
    """Raised when Celery fails to dispatch or execute the compare task."""


class CompareV2Dispatcher:
    task_name = "grc_policy_server.tasks.compare_v2"

    def __init__(self, *, upload_root: Path):
        self.cache_store = ComparisonCacheStore(upload_root=upload_root)
        self._celery = self._build_celery_app()

    def enqueue_compare(self, payload: CompareTaskPayload) -> str:
        celery_app = self._celery
        if settings.celery_enforce_worker_ping:
            self._assert_worker_available(celery_app)

        try:
            async_result = celery_app.send_task(
                self.task_name,
                args=[payload.model_dump(mode="json")],
                queue=settings.celery_default_queue,
            )
        except Exception as exc:
            raise CeleryTaskFailureError(
                f"Failed to dispatch compare task to Celery: {_format_exception(exc)}"
            ) from exc

        return str(async_result.id)

    def get_compare_status(self, *, job_id: str) -> CompareV2JobStatusResponse:
        if self.cache_store.is_cached_job_id(job_id):
            cached = self.cache_store.load_for_cached_job(job_id=job_id)
            if cached is None:
                return CompareV2JobStatusResponse(
                    jobId=job_id,
                    status="failed",
                    done=True,
                    error="Cached comparison result not found",
                    cacheHit=True,
                )
            return CompareV2JobStatusResponse(
                jobId=job_id,
                status="finished",
                done=True,
                result=cached,
                cacheHit=True,
            )

        try:
            async_result = self._celery.AsyncResult(job_id)
            state = str(async_result.state or "PENDING").upper()
        except Exception as exc:
            raise CeleryTaskFailureError(
                f"Failed to fetch compare task status from Celery: {_format_exception(exc)}"
            ) from exc

        if state in {"PENDING", "RECEIVED"}:
            return CompareV2JobStatusResponse(
                jobId=job_id,
                status="queued",
                done=False,
                cacheHit=False,
            )

        if state in {"STARTED", "RETRY"}:
            return CompareV2JobStatusResponse(
                jobId=job_id,
                status="running",
                done=False,
                cacheHit=False,
            )

        if state == "SUCCESS":
            payload = async_result.result
            if not isinstance(payload, dict):
                raise CeleryTaskFailureError(
                    "Celery compare task returned a non-object payload"
                )

            raw_comparison = payload.get("comparison")
            if not isinstance(raw_comparison, dict):
                raw_comparison = payload

            try:
                comparison = ComparisonResult.model_validate(raw_comparison)
            except Exception as exc:
                raise CeleryTaskFailureError(
                    "Celery task returned an invalid comparison response payload"
                ) from exc

            return CompareV2JobStatusResponse(
                jobId=job_id,
                status="finished",
                done=True,
                result=comparison,
                cacheHit=False,
            )

        error = str(async_result.result) if async_result.result is not None else "Compare task failed"
        return CompareV2JobStatusResponse(
            jobId=job_id,
            status="failed",
            done=True,
            error=error,
            cacheHit=False,
        )

    def _build_celery_app(self):
        try:
            from celery import Celery
        except Exception as exc:
            raise CeleryNotAvailableError(
                "Celery is not installed. Install Celery and configure broker/backend."
            ) from exc

        celery_app = Celery(
            "grc_policy_server",
            broker=settings.celery_broker_url,
            backend=settings.celery_result_backend,
        )
        celery_app.conf.update(task_default_queue=settings.celery_default_queue)
        return celery_app

    def _assert_worker_available(self, celery_app) -> None:
        try:
            inspector = celery_app.control.inspect(
                timeout=settings.celery_worker_ping_timeout_sec
            )
            ping = inspector.ping() if inspector else None
        except Exception as exc:
            raise CeleryWorkerUnavailableError(
                "Unable to verify Celery workers via broker inspection. "
                "Set CELERY_ENFORCE_WORKER_PING=false to skip this preflight check."
            ) from exc

        if not ping:
            raise CeleryWorkerUnavailableError(
                "No active Celery workers detected for compare v2 endpoint"
            )


def _format_exception(exc: Exception) -> str:
    detail = str(exc).strip()
    redis_hint = ""
    if (
        isinstance(exc, AttributeError)
        and "has no attribute 'Redis'" in detail
        and "NoneType" in detail
    ):
        redis_hint = (
            " (missing Redis Python client in runtime; install with `celery[redis]` or `redis`)"
        )

    if detail:
        return f"{type(exc).__name__}: {detail}{redis_hint}"
    return f"{type(exc).__name__}{redis_hint}"
