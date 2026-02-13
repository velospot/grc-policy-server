from fastapi import APIRouter

from grc_policy_server.models.schemas import CompareRequest, ComparisonResult
from grc_policy_server.services.comparision.diff_engine import DiffEngine
from grc_policy_server.services.comparision.real_diff_engine import RealDiffEngine

router = APIRouter()

####  mock response
# @router.post(
#     "/compare",
#     response_model=ComparisonResult,
#     summary="Compare two documents (mocked)",
# )
# def compare_documents(doc1: Document, doc2: Document):
#     """
#     Contractual skeleton endpoint.

#     This endpoint guarantees response shape stability.
#     Internal implementation will be replaced by real diff engine.
#     """

#     if doc1.id == doc2.id:
#         raise HTTPException(
#             status_code=400,
#             detail="Cannot compare the same document",
#         )

#     return mock_generate_comparison(doc1, doc2)


@router.post("/", response_model=ComparisonResult)
async def compare_documents(doc_a: str, doc_b: str):

    engine = DiffEngine()

    # placeholder chunks
    old_chunk = {"text": "Old policy text"}
    new_chunk = {"text": "New policy text"}

    diff = await engine.compare(old_chunk, new_chunk)

    return {"documents": [doc_a, doc_b], "differences": [diff]}

    router = APIRouter(prefix="/compare", tags=["compare"])


@router.post("/", response_model=CompareResponse)
async def compare_route_documents(payload: CompareRequest):

    diffs = RealDiffEngine.compare(
        payload.doc1.content,
        payload.doc2.content,
    )

    return CompareResponse(diffs=diffs)
