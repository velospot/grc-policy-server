import logging

from fastapi import APIRouter

from grc_policy_server.models.schemas import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service health check")
def health_check():
    logger.debug("health check called")
    return HealthResponse(status="ok")


@router.get(
    "/health/services",
    summary="Per-service health status",
    description=(
        "Returns the cached health status of each external service dependency. "
        "Statuses refresh every 30s (configurable). "
        "In offline mode, Weaviate and Celery report 'disabled'."
    ),
)
def health_services() -> dict:
    """Return per-service health status from the circuit-breaking registry."""
    from grc_policy_server.services.health.service_health_registry import (
        get_service_health_registry,
    )
    registry = get_service_health_registry()
    return registry.status_report()
