from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any
from pathlib import Path

from grc_policy_server.core.celery_app import celery_app
from grc_policy_server.core.config import settings
from grc_policy_server.models.schemas import UploadDocumentResponse, UploadDocumentsResponse
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient, Neo4jSettings
from grc_policy_server.services.ingestion.docling_adapter import DoclingAdapter
from grc_policy_server.services.ingestion.document_ingestion_service import (
    DocumentIngestionService,
)
from grc_policy_server.services.ingestion.upload_v2_models import UploadTaskFilePayload
from grc_policy_server.services.llm.ollama_client import OllamaClient, OllamaSettings
from grc_policy_server.services.vector.weaviate_client import WeaviateClient

logger = logging.getLogger(__name__)


def _build_ingestion_service() -> tuple[
    DocumentIngestionService,
    WeaviateClient,
    Neo4jClient | None,
    OllamaClient,
]:
    docling_adapter = DoclingAdapter()
    weaviate = WeaviateClient()
    neo4j: Neo4jClient | None = None
    if settings.neo4j_enabled:
        neo4j = Neo4jClient(
            Neo4jSettings(
                uri=settings.neo4j_uri,
                user=settings.neo4j_user,
                password=settings.neo4j_password,
                database=settings.neo4j_database,
            )
        )
    llm = OllamaClient(
        OllamaSettings(
            base_url=settings.ollama_url,
            chat_model=settings.ollama_chat_model,
            embed_model=settings.ollama_embed_model,
            read_timeout_sec=settings.ollama_timeout_sec,
        )
    )
    service = DocumentIngestionService(
        docling_adapter=docling_adapter,
        weaviate=weaviate,
        neo4j=neo4j,
        llm=llm,
        upload_root=Path(settings.upload_root),
    )
    return service, weaviate, neo4j, llm


async def _ingest_payloads(
    *,
    service: DocumentIngestionService,
    payload_files: list[UploadTaskFilePayload],
) -> UploadDocumentsResponse:
    results: list[UploadDocumentResponse] = []

    for payload in payload_files:
        if not payload.filename:
            results.append(
                UploadDocumentResponse(
                    filename="<missing>",
                    contentType=payload.content_type,
                    accepted=False,
                    error="Missing upload filename",
                )
            )
            continue

        try:
            content = base64.b64decode(payload.content_base64.encode("ascii"), validate=True)
        except Exception:
            results.append(
                UploadDocumentResponse(
                    filename=payload.filename,
                    contentType=payload.content_type,
                    accepted=False,
                    error="Failed to decode uploaded file payload",
                )
            )
            continue

        if not content:
            results.append(
                UploadDocumentResponse(
                    filename=payload.filename,
                    contentType=payload.content_type,
                    accepted=False,
                    error="Uploaded file is empty",
                )
            )
            continue

        try:
            result = await service.ingest_upload(
                filename=payload.filename,
                content=content,
                content_type=payload.content_type,
            )
        except ValueError as exc:
            results.append(
                UploadDocumentResponse(
                    filename=payload.filename,
                    contentType=payload.content_type,
                    accepted=False,
                    error=str(exc),
                )
            )
            continue
        except Exception:
            logger.exception("failed to ingest uploaded file=%s", payload.filename)
            results.append(
                UploadDocumentResponse(
                    filename=payload.filename,
                    contentType=payload.content_type,
                    accepted=False,
                    error="Failed to ingest uploaded document",
                )
            )
            continue

        results.append(
            UploadDocumentResponse(
                filename=payload.filename,
                contentType=payload.content_type,
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


@celery_app.task(name="grc_policy_server.tasks.ingest_upload_v2")
def ingest_upload_v2(payload_files: list[dict[str, Any]]) -> dict[str, Any]:
    parsed_payloads = [
        UploadTaskFilePayload.model_validate(item) for item in payload_files
    ]
    service, weaviate, neo4j, llm = _build_ingestion_service()
    try:
        response = asyncio.run(
            _ingest_payloads(
                service=service,
                payload_files=parsed_payloads,
            )
        )
        return response.model_dump(mode="json")
    finally:
        try:
            weaviate.close()
        except Exception:
            logger.exception("failed to close Weaviate client in upload_v2 task")
        try:
            if neo4j is not None:
                neo4j.close()
        except Exception:
            logger.exception("failed to close Neo4j client in upload_v2 task")
        try:
            llm.close()  # sync close — event loop is not running at this point
        except Exception:
            logger.exception("failed to close Ollama client in upload_v2 task")
