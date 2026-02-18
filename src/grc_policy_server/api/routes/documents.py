import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/upload",
)
async def upload():

    return
