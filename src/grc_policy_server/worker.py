import os
import signal
import sys

# Prevent loky/joblib from spawning process pools inside Celery workers.
# Must be set before any ML library imports to avoid leaked semaphore objects
# at shutdown (ResourceWarning + loky tracker warnings on Ctrl-C).
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

from celery.signals import worker_process_init, worker_shutdown, worker_ready  # noqa: E402

from grc_policy_server.core.celery_app import celery_app
from grc_policy_server.core.config import settings
from grc_policy_server.core.logging import logging
from grc_policy_server.services.observability import tracing

logger = logging.getLogger(__name__)


@worker_process_init.connect
def _close_inherited_stdin(**kwargs):
    """Close stdin in Celery forked workers to suppress TextIOWrapper GC warnings."""
    try:
        sys.stdin.close()
    except Exception:
        pass


@worker_ready.connect
def _install_graceful_shutdown(**kwargs):
    """Install SIGTERM handler for graceful warm shutdown.

    When the server process receives SIGTERM (e.g. Docker stop, systemd stop),
    the worker finishes running tasks before exiting rather than dropping them.
    SIGTERM → warm shutdown (finish current tasks, then exit).
    SIGINT  → preserved default (second Ctrl-C triggers immediate kill).
    """
    def _sigterm_handler(signum, frame):
        logger.info("SIGTERM received — initiating Celery warm shutdown")
        # celery_app.control.broadcast triggers a warm shutdown on the local worker
        celery_app.control.broadcast("shutdown", destination=None)

    signal.signal(signal.SIGTERM, _sigterm_handler)
    logger.info("Celery worker ready, SIGTERM graceful-shutdown handler installed")


@worker_shutdown.connect
def _on_worker_shutdown(**kwargs):
    logger.info("Celery worker shutdown initiated")


# Initialise tracing once at worker startup — gated on OPIK_ENABLED.
tracing.configure(
    enabled=settings.opik_enabled,
    url=settings.opik_url_override,
    project_name=settings.opik_project_name,
    workspace=settings.opik_workspace,
)

# Ensure tasks are registered when Celery imports this module.
import grc_policy_server.tasks.upload_v2  # noqa: F401, E402
import grc_policy_server.tasks.compare_v2  # noqa: F401, E402
import grc_policy_server.tasks.backfill_section_summaries  # noqa: F401, E402

__all__ = ["celery_app"]
