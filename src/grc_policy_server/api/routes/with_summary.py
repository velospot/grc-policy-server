from fastapi import APIRouter, Depends

from grc_policy_server.api.deps import get_diff_engine_stream
from grc_policy_server.models.schemas import (
    CompareRequest,
)
from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine

router = APIRouter()


@router.post("/with-summary")
async def compare_with_summary(
    payload: CompareRequest,
    service: RealDiffEngine = Depends(get_diff_engine_stream),
):
    return await service.compare(payload.doc1, payload.doc2)
