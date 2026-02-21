import logging
from collections.abc import Callable

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from grc_policy_server.api.deps import (
    get_document_ingestion_service_factory,
    get_document_repository,
)
from grc_policy_server.models.schemas import (
    Document,
    UploadDocumentResponse,
    UploadDocumentsResponse,
)
from grc_policy_server.respositories.documents import DocumentRepository
from grc_policy_server.services.documents.mapper import to_document_response
from grc_policy_server.services.ingestion.document_ingestion_service import (
    DocumentIngestionService,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

@router.post(
    "/upload",
    response_model=UploadDocumentsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload one or more policy documents",
)
async def upload(
    files: list[UploadFile] = File(
        ...,
        alias="file",
        description="Repeat the `file` field to upload multiple documents.",
    ),
    ingestion_service_factory: Callable[[], DocumentIngestionService] = Depends(
        get_document_ingestion_service_factory
    ),
):
    """Ingest one or more uploaded documents and report per-file outcomes."""
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files were uploaded",
        )

    ingestion_service = ingestion_service_factory()
    results: list[UploadDocumentResponse] = []

    for upload_file in files:
        if not upload_file.filename:
            results.append(
                UploadDocumentResponse(
                    filename="<missing>",
                    contentType=upload_file.content_type,
                    accepted=False,
                    error="Missing upload filename",
                )
            )
            continue

        content = await upload_file.read()
        if not content:
            results.append(
                UploadDocumentResponse(
                    filename=upload_file.filename,
                    contentType=upload_file.content_type,
                    accepted=False,
                    error="Uploaded file is empty",
                )
            )
            continue

        try:
            result = await ingestion_service.ingest_upload(
                filename=upload_file.filename,
                content=content,
                content_type=upload_file.content_type,
            )
        except ValueError as exc:
            results.append(
                UploadDocumentResponse(
                    filename=upload_file.filename,
                    contentType=upload_file.content_type,
                    accepted=False,
                    error=str(exc),
                )
            )
            continue
        except Exception:
            logger.exception("failed to ingest uploaded file=%s", upload_file.filename)
            results.append(
                UploadDocumentResponse(
                    filename=upload_file.filename,
                    contentType=upload_file.content_type,
                    accepted=False,
                    error="Failed to ingest uploaded document",
                )
            )
            continue

        results.append(
            UploadDocumentResponse(
                filename=upload_file.filename,
                contentType=upload_file.content_type,
                accepted=True,
                documentId=result.document_id,
                chunksStored=result.chunks_stored,
            )
        )

    accepted_count = sum(1 for result in results if result.accepted)
    return UploadDocumentsResponse(
        acceptedCount=accepted_count,
        rejectedCount=len(results) - accepted_count,
        results=results,
    )


@router.get(
    "",
    response_model=list[Document],
    summary="List uploaded documents",
)
def list_documents(
    repository: DocumentRepository = Depends(get_document_repository),
):
    """Return uploaded documents using metadata stored on disk."""
    documents = repository.list_documents()
    return [to_document_response(document) for document in documents]
