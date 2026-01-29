import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", tags=["health"])
def health_check():
    logger.debug("health check called")
    return {"status": "ok"}
