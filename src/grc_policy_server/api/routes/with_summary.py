from fastapi import APIRouter, Depends, HTTPException, status

from grc_policy_server.api.deps import (
    get_diff_engine_stream,
    require_api_bearer_token,
)
from grc_policy_server.core.config import settings
from grc_policy_server.models.schemas import (
    ActionItem,
    ComparisonAccuracyMetrics,
    CompareRequest,
    ComparisonResult,
    KeyDifference,
)
from grc_policy_server.services.comparison.real_diff_engine_stream import (
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
    accuracy_metrics: ComparisonAccuracyMetrics | None = None

    stream = service.compare_stream(
        payload.doc1,
        payload.doc2,
        force_re_extract=payload.forceReExtract,
    )

    async for event in stream:
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
            raw_accuracy = event.get("accuracyMetrics")
            if raw_accuracy is not None:
                accuracy_metrics = ComparisonAccuracyMetrics.model_validate(raw_accuracy)

    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Comparison stream finished without a summary payload",
        )

    audit_mode = payload.auditMode
    hidden_diffs_count = 0
    if not audit_mode:
        visible = [d for d in key_differences if d.changeSeverity != "low"]
        hidden_diffs_count = len(key_differences) - len(visible)
        key_differences = visible

    require_human_review = any(d.requiresHumanReview for d in key_differences)

    return ComparisonResult(
        summary=summary,
        keyDifferences=key_differences,
        actionPlan=action_plan,
        followUpQuestions=follow_up_questions,
        accuracyMetrics=accuracy_metrics,
        comparisonMode="auditor_grade" if audit_mode else "simple",
        requireHumanReview=require_human_review,
        hiddenDiffsCount=hidden_diffs_count,
    )
