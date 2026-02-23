import logging
from collections.abc import Callable

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from grc_policy_server.api.deps import (
    get_document_ingestion_service_factory,
    get_document_repository,
    get_weaviate_client,
    require_api_bearer_token,
)
from grc_policy_server.models.schemas import (
    DeleteDocumentResult,
    DeleteDocumentsRequest,
    DeleteDocumentsResponse,
    Document,
    UploadDocumentResponse,
    UploadDocumentsResponse,
)
from grc_policy_server.respositories.documents import DocumentRepository
from grc_policy_server.services.documents.mapper import to_document_response
from grc_policy_server.services.ingestion.document_ingestion_service import (
    DocumentIngestionService,
)
from grc_policy_server.services.vector.weaviate_client import WeaviateClient

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(require_api_bearer_token)],
)

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


@router.post(
    "/delete",
    response_model=DeleteDocumentsResponse,
    summary="Delete one or more uploaded documents",
)
def delete_documents(
    payload: DeleteDocumentsRequest,
    repository: DocumentRepository = Depends(get_document_repository),
    weaviate: WeaviateClient = Depends(get_weaviate_client),
):
    """Delete local document artifacts and associated vector records."""
    if not payload.documentIds:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No document ids were provided",
        )

    results: list[DeleteDocumentResult] = []
    seen_document_ids: set[str] = set()

    for raw_document_id in payload.documentIds:
        document_id = raw_document_id.strip()
        if not document_id:
            results.append(
                DeleteDocumentResult(
                    documentId=document_id,
                    deleted=False,
                    error="Document id must not be empty",
                )
            )
            continue

        if document_id in seen_document_ids:
            results.append(
                DeleteDocumentResult(
                    documentId=document_id,
                    deleted=False,
                    error="Duplicate document id in request",
                )
            )
            continue
        seen_document_ids.add(document_id)

        try:
            deleted_chunks = weaviate.delete_chunks_by_document(document_id)
        except Exception:
            logger.exception(
                "failed to delete weaviate records document_id=%s", document_id
            )
            results.append(
                DeleteDocumentResult(
                    documentId=document_id,
                    deleted=False,
                    error="Failed to delete document records from Weaviate",
                )
            )
            continue

        try:
            deleted_local = repository.delete_document(document_id)
        except ValueError as exc:
            results.append(
                DeleteDocumentResult(
                    documentId=document_id,
                    deleted=False,
                    deletedChunks=deleted_chunks,
                    error=str(exc),
                )
            )
            continue
        except Exception:
            logger.exception("failed to delete local document_id=%s", document_id)
            results.append(
                DeleteDocumentResult(
                    documentId=document_id,
                    deleted=False,
                    deletedChunks=deleted_chunks,
                    error="Failed to delete local document files",
                )
            )
            continue

        if not deleted_local and deleted_chunks == 0:
            results.append(
                DeleteDocumentResult(
                    documentId=document_id,
                    deleted=False,
                    deletedChunks=0,
                    error="Document not found",
                )
            )
            continue

        results.append(
            DeleteDocumentResult(
                documentId=document_id,
                deleted=True,
                deletedChunks=deleted_chunks,
            )
        )

    deleted_count = sum(1 for result in results if result.deleted)
    return DeleteDocumentsResponse(
        deletedCount=deleted_count,
        failedCount=len(results) - deleted_count,
        results=results,
    )
