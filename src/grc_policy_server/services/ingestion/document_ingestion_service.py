from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from docling_core.transforms.chunker.doc_chunk import DocChunk

from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.ingestion.docling_adapter import DoclingAdapter
from grc_policy_server.services.ingestion.docling_chunker import (
    chunk_document,
    extract_basic_chunk_fields,
)
from grc_policy_server.services.llm.ollama_client import OllamaClient
from grc_policy_server.services.vector.weaviate_client import WeaviateClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadIngestionResult:
    """Identifiers returned after a document is successfully ingested."""

    document_id: str
    chunks_stored: int


class DocumentIngestionService:
    """Converts uploaded files into chunks and stores metadata/index entries."""

    def __init__(
        self,
        *,
        docling_adapter: DoclingAdapter,
        weaviate: WeaviateClient,
        neo4j: Neo4jClient,
        llm: OllamaClient,
        upload_root: Path,
    ):
        self.docling_adapter = docling_adapter
        self.weaviate = weaviate
        self.neo4j = neo4j
        self.llm = llm
        self.upload_root = upload_root

    async def ingest_upload(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None = None,
    ) -> UploadIngestionResult:
        """Convert an uploaded document, store chunks, and persist upload metadata."""
        document_id = str(uuid4())
        dl_doc = self.docling_adapter.convert_bytes(
            filename=filename,
            content=content,
            auto_ocr=True,
            force_full_page_ocr=False,
            do_table_structure=True,
        )

        raw_chunks = chunk_document(dl_doc, merge_list_items=True)
        chunks_to_store: list[dict] = []
        doc_to_store: list[dict] = []

        for idx, raw_chunk in enumerate(raw_chunks):
            fields = extract_basic_chunk_fields(raw_chunk)
            doc_chunk = DocChunk.model_validate(raw_chunk)
            doc_to_store.append(doc_chunk.export_json_dict())
            text = (fields.get("text") or "").strip()
            if not text:
                continue

            section_titles = fields.get("section_path") or []
            section_path = (
                " / ".join(section_titles) if section_titles else "Unknown Section"
            )

            chunks_to_store.append(
                {
                    "chunk_id": f"{document_id}:{idx}",
                    "document_id": document_id,
                    "section_path": section_path,
                    "text": text,
                    "chunk_index": idx,
                    "page_number": fields.get("page_number"),
                    "line_start": None,
                    "line_end": None,
                }
            )

        if not chunks_to_store:
            raise ValueError("No text chunks produced from uploaded document")

        self.weaviate.upsert_chunks(chunks_to_store)
        self.neo4j.upsert_document_with_chunks(
            document_id=document_id,
            filename=filename,
            chunks=chunks_to_store,
        )
        self._persist_upload_metadata(
            document_id=document_id,
            filename=filename,
            content=content,
            content_type=content_type,
        )

        logger.info(
            "ingested upload document_id=%s filename=%s chunks=%s",
            document_id,
            filename,
            len(chunks_to_store),
        )

        return UploadIngestionResult(
            document_id=document_id,
            chunks_stored=len(chunks_to_store),
        )

    def _persist_upload_metadata(
        self,
        *,
        document_id: str,
        filename: str,
        content: bytes,
        content_type: str | None,
    ) -> None:
        """Persist the original file and metadata under the upload root."""
        target_dir = self.upload_root / document_id
        target_dir.mkdir(parents=True, exist_ok=True)

        stored_file = target_dir / filename
        stored_file.write_bytes(content)

        metadata = {
            "id": document_id,
            "name": filename,
            "version": "1.0",
            "upload_date": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "size_bytes": len(content),
            "category": (content_type or "upload").split("/")[0],
            "stored_filename": filename,
        }
        (target_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )
