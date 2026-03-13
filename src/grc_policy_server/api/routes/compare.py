from fastapi import APIRouter, Depends

from grc_policy_server.api.deps import get_diff_engine, require_api_bearer_token
from grc_policy_server.models.schemas import CompareRequest, ComparisonResult
from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine

router = APIRouter(
    prefix="/compare",
    tags=["compare"],
    dependencies=[Depends(require_api_bearer_token)],
)


@router.post(
    "",
    response_model=ComparisonResult,
    summary="Compare two policy documents",
)
async def compare_documents(
    payload: CompareRequest,
    service: RealDiffEngine = Depends(get_diff_engine),
):
    return await service.compare(
        payload.doc1,
        payload.doc2,
        force_re_extract=payload.forceReExtract,
    )
