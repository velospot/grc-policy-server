from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from grc_policy_server.api.deps import get_diff_engine_stream, require_api_bearer_token
from grc_policy_server.models.schemas import CompareRequest
from grc_policy_server.services.comparison.real_diff_engine_stream import RealDiffEngineStream

router = APIRouter(
    prefix="/v3",
    tags=["compare-v3"],
    dependencies=[Depends(require_api_bearer_token)],
)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@router.post(
    "/compare/stream",
    summary="Stream comparison results as Server-Sent Events",
    description="""
Streams comparison progress and results as SSE (text/event-stream).

**Event types**:
- `{"type":"progress","stage":"load_canonical_nodes"}` — pipeline started
- `{"type":"progress","stage":"structured_change_records_ready","diffs":N}` — N diffs classified
- `{"type":"diff","item":{...}}` — one diff item (KeyDifference payload)
- `{"type":"progress","stage":"finalizing"}` — generating summaries
- `{"type":"done","summary":"...","actionPlan":[...],"followUpQuestions":[...],"accuracyMetrics":{...}}` — complete
- `{"type":"error","error":"..."}` — comparison failed
""",
)
async def compare_v3_stream(
    payload: CompareRequest,
    service: RealDiffEngineStream = Depends(get_diff_engine_stream),
) -> StreamingResponse:
    async def event_generator():
        try:
            async for event in service.compare_stream(
                payload.doc1,
                payload.doc2,
                force_re_extract=getattr(payload, "forceReExtract", False),
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
