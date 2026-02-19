import logging
from collections.abc import Callable

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from grc_policy_server.api.deps import (
    get_document_ingestion_service_factory,
    get_document_repository,
)
from grc_policy_server.models.schemas import Document, UploadDocumentResponse
from grc_policy_server.respositories.documents import DocumentRepository
from grc_policy_server.services.documents.mapper import to_document_response
from grc_policy_server.services.ingestion.document_ingestion_service import (
    DocumentIngestionService,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

@router.post(
    "/upload",
    response_model=UploadDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a policy document",
)
async def upload(
    file: UploadFile = File(...),
    ingestion_service_factory: Callable[[], DocumentIngestionService] = Depends(
        get_document_ingestion_service_factory
    ),
):
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing upload filename",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    ingestion_service = ingestion_service_factory()

    try:
        result = await ingestion_service.ingest_upload(
            filename=file.filename,
            content=content,
            content_type=file.content_type,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("failed to ingest uploaded file=%s", file.filename)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest uploaded document",
        )

    return UploadDocumentResponse(
        filename=file.filename,
        contentType=file.content_type,
        accepted=True,
        documentId=result.document_id,
        chunksStored=result.chunks_stored,
    )


@router.get(
    "",
    response_model=list[Document],
    summary="List uploaded documents",
)
def list_documents(
    repository: DocumentRepository = Depends(get_document_repository),
):
    documents = repository.list_documents()
    return [to_document_response(document) for document in documents]
