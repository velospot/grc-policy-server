from grc_policy_server.core.celery_app import celery_app

# Ensure tasks are registered when Celery imports this module.
import grc_policy_server.tasks.upload_v2  # noqa: F401
import grc_policy_server.tasks.compare_v2  # noqa: F401

__all__ = ["celery_app"]
