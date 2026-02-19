import logging

from fastapi import APIRouter

from grc_policy_server.models.schemas import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service health check")
def health_check():
    logger.debug("health check called")
    return HealthResponse(status="ok")
