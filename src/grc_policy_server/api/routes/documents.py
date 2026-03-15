import logging
import base64
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from grc_policy_server.api.deps import (
    get_document_ingestion_service_factory,
    get_document_repository,
    get_neo4j_client,
    get_upload_v2_dispatcher,
    get_weaviate_client,
    require_api_bearer_token,
)
from grc_policy_server.models.schemas import (
    DeleteDocumentResult,
    DeleteDocumentsRequest,
    DeleteDocumentsResponse,
    Document,
    HybridSearchChunk,
    HybridSearchDocumentResult,
    HybridSearchRequest,
    HybridSearchResponse,
    UploadDocumentResponse,
    UploadDocumentsResponse,
    UploadV2JobCreateResponse,
    UploadV2JobStatusResponse,
)
from grc_policy_server.respositories.documents import DocumentRepository
from grc_policy_server.services.documents.mapper import to_document_response
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.ingestion.document_ingestion_service import (
    DocumentIngestionService,
)
from grc_policy_server.services.ingestion.upload_v2_dispatcher import (
    CeleryNotAvailableError,
    CeleryTaskFailureError,
    CeleryWorkerUnavailableError,
    UploadV2Dispatcher,
)
from grc_policy_server.services.ingestion.upload_v2_models import UploadTaskFilePayload
from grc_policy_server.services.vector.weaviate_client import WeaviateClient

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(require_api_bearer_token)],
)


def _to_hybrid_search_chunks(chunks: list[dict[str, Any]]) -> list[HybridSearchChunk]:
    out: list[HybridSearchChunk] = []
    for chunk in chunks:
        chunk_index_raw = chunk.get("chunk_index")
        chunk_index: int | None = None
        if isinstance(chunk_index_raw, int):
            chunk_index = chunk_index_raw
        elif isinstance(chunk_index_raw, float):
            chunk_index = int(chunk_index_raw)

        score_raw = chunk.get("_distance")
        score: float | None = None
        if isinstance(score_raw, (int, float)):
            score = float(score_raw)

        out.append(
            HybridSearchChunk(
                chunkId=str(chunk.get("chunk_id") or ""),
                documentId=str(chunk.get("document_id") or ""),
                sectionPath=str(chunk.get("section_path") or ""),
                text=str(chunk.get("text") or ""),
                chunkIndex=chunk_index,
                score=score,
            )
        )
    return out


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


@router.post(
    "/upload/v2",
    response_model=UploadV2JobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue upload ingestion job (Celery worker)",
)
async def upload_v2(
    files: list[UploadFile] = File(
        ...,
        alias="file",
        description="Repeat the `file` field to upload multiple documents.",
    ),
    dispatcher: UploadV2Dispatcher = Depends(get_upload_v2_dispatcher),
):
    """Queue one upload-ingestion job and return a job id for polling."""
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files were uploaded",
        )

    payload_files: list[UploadTaskFilePayload] = []
    for upload_file in files:
        content = await upload_file.read()
        payload_files.append(
            UploadTaskFilePayload(
                filename=upload_file.filename or "",
                content_type=upload_file.content_type,
                content_base64=base64.b64encode(content).decode("ascii"),
            )
        )

    try:
        job_id = dispatcher.enqueue_uploads(payload_files)
        return UploadV2JobCreateResponse(jobId=job_id)
    except CeleryNotAvailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except CeleryWorkerUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except CeleryTaskFailureError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@router.get(
    "/upload/v2/{job_id}",
    response_model=UploadV2JobStatusResponse,
    summary="Poll upload v2 job status",
)
def upload_v2_status(
    job_id: str,
    dispatcher: UploadV2Dispatcher = Depends(get_upload_v2_dispatcher),
):
    """Return current status for an upload v2 job id."""
    if not job_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="job_id must not be empty",
        )

    try:
        return dispatcher.get_upload_status(job_id=job_id.strip())
    except CeleryNotAvailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except CeleryTaskFailureError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


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
    neo4j: Neo4jClient | None = Depends(get_neo4j_client),
):
    """Delete local document artifacts and associated vector and graph records."""
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

        deleted_graph_nodes = 0
        if neo4j is not None:
            try:
                deleted_graph_nodes = neo4j.delete_document_subgraph(document_id)
            except Exception:
                logger.exception("failed to delete graph records document_id=%s", document_id)
                results.append(
                    DeleteDocumentResult(
                        documentId=document_id,
                        deleted=False,
                        deletedChunks=deleted_chunks,
                        error="Failed to delete document records from Neo4j",
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

        if not deleted_local and deleted_chunks == 0 and deleted_graph_nodes == 0:
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


@router.post(
    "/search/hybrid",
    response_model=HybridSearchResponse,
    summary="Hybrid-search chunks in two documents",
)
def hybrid_search_documents(
    payload: HybridSearchRequest,
    weaviate: WeaviateClient = Depends(get_weaviate_client),
):
    """Run hybrid retrieval in Weaviate for two documents and return matched chunks."""
    document_id_1 = payload.documentId1.strip()
    document_id_2 = payload.documentId2.strip()
    query_string = payload.query.strip()

    if not document_id_1 or not document_id_2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both document ids are required",
        )
    if document_id_1 == document_id_2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="documentId1 and documentId2 must be different",
        )
    if not query_string:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query must not be empty",
        )

    try:
        doc1_matches = weaviate.hybrid_search_in_document(
            query_string=query_string,
            target_document_id=document_id_1,
            limit=payload.limit,
        )
        doc2_matches = weaviate.hybrid_search_in_document(
            query_string=query_string,
            target_document_id=document_id_2,
            limit=payload.limit,
        )
    except Exception:
        logger.exception(
            "failed to run hybrid search document_id_1=%s document_id_2=%s",
            document_id_1,
            document_id_2,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to run hybrid search in Weaviate",
        )

    return HybridSearchResponse(
        query=query_string,
        results=[
            HybridSearchDocumentResult(
                documentId=document_id_1,
                chunks=_to_hybrid_search_chunks(doc1_matches),
            ),
            HybridSearchDocumentResult(
                documentId=document_id_2,
                chunks=_to_hybrid_search_chunks(doc2_matches),
            ),
        ],
    )
