from __future__ import annotations

from grc_policy_server.core.config import settings
from grc_policy_server.models.schemas import (
    UploadDocumentsResponse,
    UploadV2JobStatusResponse,
)
from grc_policy_server.services.ingestion.upload_v2_models import UploadTaskFilePayload


class CeleryNotAvailableError(RuntimeError):
    """Raised when Celery is not installed in the runtime."""


class CeleryWorkerUnavailableError(RuntimeError):
    """Raised when no Celery worker is available to process the task."""


class CeleryTaskFailureError(RuntimeError):
    """Raised when Celery fails to dispatch or execute the upload task."""


class UploadV2Dispatcher:
    task_name = "grc_policy_server.tasks.ingest_upload_v2"

    def enqueue_uploads(
        self,
        payload_files: list[UploadTaskFilePayload],
    ) -> str:
        celery_app = self._build_celery_app()
        if settings.celery_enforce_worker_ping:
            self._assert_worker_available(celery_app)

        payload = [item.model_dump() for item in payload_files]
        try:
            async_result = celery_app.send_task(
                self.task_name,
                args=[payload],
                queue=settings.celery_default_queue,
            )
        except Exception as exc:
            raise CeleryTaskFailureError(
                f"Failed to dispatch upload task to Celery: {_format_exception(exc)}"
            ) from exc

        return str(async_result.id)

    def get_upload_status(self, job_id: str) -> UploadV2JobStatusResponse:
        celery_app = self._build_celery_app()
        try:
            async_result = celery_app.AsyncResult(job_id)
            state = str(async_result.state or "PENDING").upper()
        except Exception as exc:
            raise CeleryTaskFailureError(
                f"Failed to fetch upload task status from Celery: {_format_exception(exc)}"
            ) from exc

        if state in {"PENDING", "RECEIVED"}:
            return UploadV2JobStatusResponse(
                jobId=job_id,
                status="queued",
                done=False,
            )

        if state in {"STARTED", "RETRY"}:
            return UploadV2JobStatusResponse(
                jobId=job_id,
                status="running",
                done=False,
            )

        if state == "SUCCESS":
            try:
                result = UploadDocumentsResponse.model_validate(async_result.result)
            except Exception as exc:
                raise CeleryTaskFailureError(
                    "Celery task returned an invalid upload response payload"
                ) from exc
            return UploadV2JobStatusResponse(
                jobId=job_id,
                status="finished",
                done=True,
                result=result,
            )

        error = str(async_result.result) if async_result.result is not None else "Upload task failed"
        return UploadV2JobStatusResponse(
            jobId=job_id,
            status="failed",
            done=True,
            error=error,
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
        celery_app.conf.update(
            task_default_queue=settings.celery_default_queue,
        )
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
                "No active Celery workers detected for upload v2 endpoint"
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
