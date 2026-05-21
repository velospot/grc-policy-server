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
    summary="Stream department-specific comparison results as Server-Sent Events",
    description="""
Streams comparison progress and results as SSE (text/event-stream).

This endpoint accepts a minimal payload (`doc1Id`, `doc2Id`, `testingDepartment`)
and uses department-specific prompts to classify changes into structured change records.
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
            async for event in service.compare_stream(
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

