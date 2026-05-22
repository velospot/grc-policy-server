from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from grc_policy_server.api.deps import (
    get_diff_engine_stream,
    get_document_repository,
    require_api_bearer_token,
)
from grc_policy_server.models.schemas import CompareStreamV4Request
from grc_policy_server.repositories.documents import DocumentRepository
from grc_policy_server.services.comparison.real_diff_engine_stream import RealDiffEngineStream
from grc_policy_server.services.documents.mapper import to_document_response

router = APIRouter(
    prefix="/v4",
    tags=["compare-v4"],
    dependencies=[Depends(require_api_bearer_token)],
)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@router.post(
    "/compare/stream",
    summary="Stream department-specific comparison results as Server-Sent Events (two-stage)",
    description="""
Streams a two-stage department-aware comparison as SSE (`text/event-stream`).

**Request body**
```json
{ "doc1Id": "...", "doc2Id": "...", "testingDepartment": "EMC" | "Safety" | "Environment", "forceReExtract": false }
```

**Stage 1 — Per-diff table rows**

For each classified difference the LLM streams one concise semantic explanation
(department-aware, 1–3 sentences). Cosmetic-only diffs are silently skipped; ambiguous
diffs are flagged for human review.

**Stage 2 — Aggregated summary**

After all diffs are processed, a single narrative summary is streamed.

**Event sequence**

| Event type | Key fields | Notes |
|---|---|---|
| `payload` | `doc1_id`, `doc2_id`, `testing_department` | First event |
| `progress` | `stage`, `message`, `total?` | Stage transitions: `loading` → `streaming_diffs` → `summarizing` |
| `diff_start` | `change_id`, `section`, `page`, `change_type`, `node_type`, `doc1_preview`, `doc2_preview` | Before LLM call for this diff |
| `diff_token` | `change_id`, `token` | LLM token stream (one event per token) |
| `diff_complete` | `change_id`, `row_markdown`, `requires_review`, `skipped` | Row result; `skipped=true` means no semantic change found |
| `table_complete` | `markdown`, `rows_analyzed`, `rows_skipped`, `review_count` | Full assembled markdown diff table |
| `summary_token` | `token` | LLM summary token |
| `summary_complete` | `text` | Full aggregated summary text |
| `done` | `total_diffs`, `analyzed`, `skipped`, `review_count`, `requires_human_review`, `accuracy_metrics` | Final event |
| `error` | `error` | On exception |

**Table markdown format** (assembled from `diff_complete.row_markdown` values)
```
| Section | Page | Change | Semantic Difference |
|---------|------|--------|---------------------|
| 4.2.1 Grenzwerte | p.23 | Modified | Limit tightened from 79 dBµA to 73 dBµA. Retest required. |
| 5.1 Test Setup   | p.31 | Added    | Pre-conditioning at 40 °C/2 h now mandatory. |
| —                | —    | Modified | ⚠ HUMAN_REVIEW: sections 3.4 and 3.8 overlap — cannot determine precedence. |
```

Rows prefixed with `⚠` require human review. Cosmetic-only diffs are omitted.
""",
)
async def compare_v4_stream(
    payload: CompareStreamV4Request,
    service: RealDiffEngineStream = Depends(get_diff_engine_stream),
    document_repo: DocumentRepository = Depends(get_document_repository),
) -> StreamingResponse:
    doc1_id = payload.doc1Id.strip()
    doc2_id = payload.doc2Id.strip()
    if not doc1_id or not doc2_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="doc1Id and doc2Id must not be empty",
        )
    if doc1_id == doc2_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="doc1Id and doc2Id must be different",
        )

    doc1_domain = document_repo.get_document(doc1_id)
    doc2_domain = document_repo.get_document(doc2_id)
    if doc1_domain is None or doc2_domain is None:
        missing = []
        if doc1_domain is None:
            missing.append("doc1Id")
        if doc2_domain is None:
            missing.append("doc2Id")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document(s) not found for: {', '.join(missing)}",
        )

    doc1 = to_document_response(doc1_domain)
    doc2 = to_document_response(doc2_domain)

    async def event_generator():
        try:
            async for event in service.compare_stream_v4(
                doc1,
                doc2,
                force_re_extract=payload.forceReExtract,
                testing_department=payload.testingDepartment,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )

