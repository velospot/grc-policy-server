from grc_policy_server.core.celery_app import celery_app
from grc_policy_server.core.config import settings
from grc_policy_server.services.observability import tracing

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
