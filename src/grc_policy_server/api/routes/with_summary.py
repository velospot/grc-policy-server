from fastapi import APIRouter, Depends, HTTPException, status

from grc_policy_server.api.deps import (
    get_diff_engine_stream,
    require_api_bearer_token,
)
from grc_policy_server.models.schemas import (
    ActionItem,
    CompareRequest,
    ComparisonResult,
    KeyDifference,
)
from grc_policy_server.services.comparision.real_diff_engine_stream import (
    RealDiffEngineStream,
)

router = APIRouter(
    prefix="/compare",
    tags=["compare"],
    dependencies=[Depends(require_api_bearer_token)],
)


@router.post(
    "/with-summary",
    response_model=ComparisonResult,
    summary="Compare documents and return summarized result",
)
async def compare_with_summary(
    payload: CompareRequest,
    service: RealDiffEngineStream = Depends(get_diff_engine_stream),
):
    key_differences: list[KeyDifference] = []
    summary: str | None = None
    action_plan: list[ActionItem] = []
    follow_up_questions: list[str] = []

    async for event in service.compare_stream(
        payload.doc1,
        payload.doc2,
        force_re_extract=payload.forceReExtract,
    ):
        event_type = event.get("type")

        if event_type == "diff" and "item" in event:
            key_differences.append(KeyDifference.model_validate(event["item"]))

        if event_type == "done":
            summary = str(event.get("summary") or "")
            action_plan = [
                ActionItem.model_validate(item)
                for item in event.get("actionPlan", [])
            ]
            follow_up_questions = [
                str(question) for question in event.get("followUpQuestions", [])
            ]

    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Comparison stream finished without a summary payload",
        )

    return ComparisonResult(
        summary=summary,
        keyDifferences=key_differences,
        actionPlan=action_plan,
        followUpQuestions=follow_up_questions,
    )
