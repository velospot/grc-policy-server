from fastapi import APIRouter, Depends

from grc_policy_server.api.deps import get_diff_engine
from grc_policy_server.models.schemas import CompareRequest, ComparisonResult
from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine

router = APIRouter(prefix="/compare", tags=["compare"])


@router.post(
    "",
    response_model=ComparisonResult,
    summary="Compare two policy documents",
)
async def compare_documents(
    payload: CompareRequest,
    service: RealDiffEngine = Depends(get_diff_engine),
):
    return await service.compare(payload.doc1, payload.doc2)
