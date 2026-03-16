from __future__ import annotations

from celery import Celery

from grc_policy_server.core.config import settings


celery_app = Celery(
    "grc_policy_server",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_default_queue=settings.celery_default_queue,
    task_default_delivery_mode="persistent",
    task_track_started=settings.celery_task_track_started,
    task_acks_late=True,
    task_reject_on_worker_lost=settings.celery_task_reject_on_worker_lost,
    task_soft_time_limit=settings.celery_task_soft_time_limit_sec,
    task_time_limit=settings.celery_task_hard_time_limit_sec,
    result_expires=settings.celery_result_expires_sec,
    enable_utc=True,
    timezone="UTC",
    broker_connection_retry_on_startup=settings.celery_broker_connection_retry_on_startup,
    broker_pool_limit=settings.celery_broker_pool_limit,
    worker_prefetch_multiplier=settings.celery_worker_prefetch_multiplier,
    worker_concurrency=settings.celery_worker_concurrency,
    worker_pool=settings.celery_worker_pool,
    worker_disable_rate_limits=settings.celery_worker_disable_rate_limits,
    worker_max_tasks_per_child=settings.celery_worker_max_tasks_per_child,
    worker_max_memory_per_child=settings.celery_worker_max_memory_per_child_kb,
    imports=(
        "grc_policy_server.tasks.upload_v2",
        "grc_policy_server.tasks.compare_v2",
    ),
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)
