from __future__ import annotations

import json
import logging
from dataclasses import replace
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from grc_policy_server.core.config import settings
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.ingestion.docling_adapter import DoclingAdapter
from grc_policy_server.services.ingestion.docling_chunker import (
    chunk_document,
    parse_docling_chunks,
)
from grc_policy_server.services.comparision.policy_semantics import meaning_to_metadata
from grc_policy_server.services.ingestion.hierarchy_builder import build_document_hierarchy
from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk
from grc_policy_server.services.ingestion.ocr_fallback import build_ocr_fallback_chunks
from grc_policy_server.services.ingestion.policy_preprocessor import (
    preprocess_parsed_chunks,
)
from grc_policy_server.services.llm.ollama_client import OllamaClient
from grc_policy_server.services.vector.weaviate_client import WeaviateClient
from grc_policy_server.utils.hashing import sha256_hex

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
        doc_json = dl_doc.export_to_dict()
        content_hash = sha256_hex(content)

        raw_chunks = chunk_document(dl_doc, merge_list_items=True)
        parsed_chunks = parse_docling_chunks(dl_doc, raw_chunks)
        parsed_chunks, ocr_metadata = self._apply_ocr_fallback(
            filename=filename,
            content=content,
            page_count=len(getattr(dl_doc, "pages", {}) or {}),
            parsed_chunks=parsed_chunks,
        )
        parsed_chunks = preprocess_parsed_chunks(parsed_chunks)
        parsed_chunks = await self._enrich_clause_semantics(parsed_chunks)

        hierarchy = build_document_hierarchy(
            document_id=document_id,
            filename=filename,
            parsed_chunks=parsed_chunks,
            content_hash=content_hash,
        )
        vector_records = [node.to_vector_record() for node in hierarchy.indexable_nodes]
        if not vector_records:
            raise ValueError("No indexable text nodes produced from uploaded document")

        self.weaviate.upsert_chunks(vector_records)
        self.neo4j.upsert_document_hierarchy(
            document_id=document_id,
            filename=filename,
            document_stable_id=hierarchy.document_stable_id,
            document_family=hierarchy.document_family,
            content_hash=content_hash,
            nodes=[node.to_graph_record() for node in hierarchy.nodes],
            metadata={
                **hierarchy.metadata,
                "ocr": ocr_metadata,
                "content_type": content_type,
            },
        )
        self._persist_upload_metadata(
            document_id=document_id,
            filename=filename,
            content=content,
            content_type=content_type,
            docling_doc=doc_json,
            hierarchy={
                "documentStableId": hierarchy.document_stable_id,
                "documentFamily": hierarchy.document_family,
                "contentHash": hierarchy.content_hash,
                "metadata": hierarchy.metadata,
                "nodes": [node.to_graph_record() for node in hierarchy.nodes],
            },
            ocr_metadata=ocr_metadata,
            chunks_stored=len(vector_records),
        )

        logger.info(
            "ingested upload document_id=%s filename=%s nodes=%s indexed=%s",
            document_id,
            filename,
            len(hierarchy.nodes),
            len(vector_records),
        )

        return UploadIngestionResult(
            document_id=document_id,
            chunks_stored=len(vector_records),
        )

    async def _enrich_clause_semantics(
        self,
        parsed_chunks: list[ParsedChunk],
    ) -> list[ParsedChunk]:
        clause_indexes: list[int] = []
        clause_texts: list[str] = []

        for index, chunk in enumerate(parsed_chunks):
            if chunk.chunk_type != "clause":
                continue
            text = (chunk.text or "").strip()
            if not text:
                continue
            clause_indexes.append(index)
            clause_texts.append(text)

        if not clause_texts:
            return parsed_chunks

        extracted = await self.llm.extract_policy_meanings(texts=clause_texts)
        enriched = list(parsed_chunks)
        for chunk_index, meaning in zip(clause_indexes, extracted, strict=False):
            chunk = enriched[chunk_index]
            metadata = dict(chunk.metadata)
            metadata.update(meaning_to_metadata(chunk.text))
            metadata.update({key: str(value or "") for key, value in meaning.items()})
            metadata["semantic_source"] = "ollama"
            enriched[chunk_index] = replace(chunk, metadata=metadata)
        return enriched

    def _apply_ocr_fallback(
        self,
        *,
        filename: str,
        content: bytes,
        page_count: int,
        parsed_chunks: list[ParsedChunk],
    ) -> tuple[list[ParsedChunk], dict[str, Any]]:
        if not settings.ocr_fallback_enabled:
            return parsed_chunks, {"enabled": False, "used": False, "reason": "disabled"}

        ocr_chunks, ocr_metadata, ocr_pages = build_ocr_fallback_chunks(
            filename=filename,
            content=content,
            parsed_chunks=parsed_chunks,
            page_count=page_count,
            min_chars_per_page=settings.ocr_fallback_min_chars_per_page,
            min_total_chars=settings.ocr_fallback_min_total_chars,
            render_dpi=settings.ocr_fallback_render_dpi,
            languages=settings.ocr_fallback_languages,
            page_segmentation_mode=settings.ocr_fallback_page_segmentation_mode,
        )
        if not ocr_pages:
            return parsed_chunks, ocr_metadata

        filtered_chunks = [
            chunk
            for chunk in parsed_chunks
            if not self._should_replace_docling_chunk_with_ocr(chunk, ocr_pages)
        ]
        filtered_chunks.extend(ocr_chunks)
        filtered_chunks.sort(key=lambda chunk: (chunk.page_number or 0, chunk.ordinal))
        return filtered_chunks, ocr_metadata

    def _should_replace_docling_chunk_with_ocr(
        self,
        chunk: ParsedChunk,
        ocr_pages: set[int],
    ) -> bool:
        return (
            chunk.source == "docling"
            and chunk.page_number in ocr_pages
            and chunk.chunk_type == "clause"
            and len((chunk.text or "").strip())
            < settings.ocr_fallback_min_chars_per_page
        )

    def _persist_upload_metadata(
        self,
        *,
        document_id: str,
        filename: str,
        content: bytes,
        content_type: str | None,
        docling_doc: dict[str, Any],
        hierarchy: dict[str, Any],
        ocr_metadata: dict[str, Any],
        chunks_stored: int,
    ) -> None:
        """Persist the original file and metadata under the upload root."""
        target_dir = self.upload_root / document_id
        target_dir.mkdir(parents=True, exist_ok=True)

        stored_file = target_dir / filename
        stored_file.write_bytes(content)

        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        docling_filename = f"{filename}_{timestamp}.docling.json"

        metadata = {
            "id": document_id,
            "name": filename,
            "version": "1.0",
            "upload_date": timestamp,
            "size_bytes": len(content),
            "category": (content_type or "upload").split("/")[0],
            "stored_filename": filename,
            "chunks_stored": chunks_stored,
            "document_stable_id": hierarchy.get("documentStableId"),
            "document_family": hierarchy.get("documentFamily"),
            "ocr": ocr_metadata,
        }
        (target_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )
        (target_dir / docling_filename).write_text(
            json.dumps(docling_doc, indent=2),
            encoding="utf-8",
        )
        (target_dir / "hierarchy.json").write_text(
            json.dumps(hierarchy, indent=2),
            encoding="utf-8",
        )
