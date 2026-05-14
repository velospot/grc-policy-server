from __future__ import annotations

import json
import logging
import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from grc_policy_server.core.config import settings
from grc_policy_server.services.comparison.policy_semantics import meaning_to_metadata
from grc_policy_server.services.documents.canonical_store import CanonicalDocumentStore
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.ingestion.docling_adapter import DoclingAdapter
from grc_policy_server.services.ingestion.docling_chunker import (
    chunk_document,
    parse_docling_chunks,
)
from grc_policy_server.services.ingestion.hierarchy_builder import (
    build_document_hierarchy,
)
from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk
from grc_policy_server.services.ingestion.ocr_fallback import build_ocr_fallback_chunks
from grc_policy_server.services.ingestion.opendataloader_adapter import OpenDataLoaderAdapter
from grc_policy_server.services.ingestion.opendataloader_chunker import (
    parse_opendataloader_elements,
)
from grc_policy_server.services.ingestion.document_family_profile import get_profile_for_document
from grc_policy_server.services.ingestion.table_quality_enhancer import (
    enhance_table_chunks,
    filter_degenerate_table_chunks,
)
from grc_policy_server.services.ingestion.policy_preprocessor import (
    preprocess_parsed_chunks,
)
from grc_policy_server.services.llm.base import BaseLLM
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
        neo4j: Neo4jClient | None,
        llm: BaseLLM,
        upload_root: Path,
        canonical_store: CanonicalDocumentStore | None = None,
        opendataloader_adapter: OpenDataLoaderAdapter | None = None,
    ):
        self.docling_adapter = docling_adapter
        self.weaviate = weaviate
        self.neo4j = neo4j
        self.llm = llm
        self.upload_root = upload_root
        self.canonical_store = canonical_store
        self.opendataloader_adapter = opendataloader_adapter

    async def ingest_upload(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None = None,
    ) -> UploadIngestionResult:
        """Convert an uploaded document, store chunks, and persist upload metadata."""
        document_id = str(uuid4())
        content_hash = sha256_hex(content)

        parsed_chunks, ocr_metadata, doc_json, opd_elements = await self._extract_parsed_chunks(
            filename=filename,
            content=content,
        )
        self._log_extraction_score(filename, parsed_chunks)
        parsed_chunks = preprocess_parsed_chunks(parsed_chunks)
        parsed_chunks = await self._enrich_clause_semantics(parsed_chunks)

        hierarchy = build_document_hierarchy(
            document_id=document_id,
            filename=filename,
            parsed_chunks=parsed_chunks,
            content_hash=content_hash,
        )
        normalized_tree = {
            "documentStableId": hierarchy.document_stable_id,
            "documentFamily": hierarchy.document_family,
            "contentHash": hierarchy.content_hash,
            "metadata": {
                **hierarchy.metadata,
                "ocr": ocr_metadata,
                "content_type": content_type,
            },
            "nodes": [node.to_graph_record() for node in hierarchy.nodes],
        }
        vector_records = [node.to_vector_record() for node in hierarchy.indexable_nodes]
        if not vector_records:
            raise ValueError("No indexable text nodes produced from uploaded document")

        if self.canonical_store is not None:
            self.canonical_store.save_document(
                document_id=document_id,
                filename=filename,
                content_hash=content_hash,
                docling_json=doc_json,
                hierarchy=normalized_tree,
                metadata=normalized_tree["metadata"],
                opd_elements=opd_elements,
            )

        self.weaviate.upsert_chunks(vector_records)
        if self.neo4j is not None:
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
            # docling_doc=doc_json,
            hierarchy=normalized_tree,
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

    @staticmethod
    def _log_extraction_score(filename: str, chunks: list[ParsedChunk]) -> None:
        if not chunks:
            logger.info("extraction score filename=%s total=0", filename)
            return

        source = chunks[0].source
        by_type: dict[str, int] = {}
        text_lengths: list[int] = []
        tables_with_headers = 0
        chunks_with_section = 0

        for chunk in chunks:
            by_type[chunk.chunk_type] = by_type.get(chunk.chunk_type, 0) + 1
            text = (chunk.text or "").strip()
            if text:
                text_lengths.append(len(text))
            if chunk.chunk_type == "table" and (chunk.metadata.get("table_headers") or []):
                tables_with_headers += 1
            if chunk.section_path:
                chunks_with_section += 1

        total = len(chunks)
        clauses = by_type.get("clause", 0)
        tables = by_type.get("table", 0)
        headings = by_type.get("heading", 0)
        figures = by_type.get("figure", 0)
        avg_len = int(sum(text_lengths) / len(text_lengths)) if text_lengths else 0
        content_pct = int(100 * (clauses + tables) / total) if total else 0
        section_pct = int(100 * chunks_with_section / total) if total else 0

        logger.info(
            "extraction score filename=%s source=%s total=%d "
            "clauses=%d tables=%d headings=%d figures=%d "
            "avg_text_len=%d tables_with_headers=%d/%d "
            "content_ratio=%d%% section_coverage=%d%%",
            filename, source, total,
            clauses, tables, headings, figures,
            avg_len, tables_with_headers, tables,
            content_pct, section_pct,
        )

    async def _extract_parsed_chunks(
        self,
        *,
        filename: str,
        content: bytes,
    ) -> tuple[list[ParsedChunk], dict[str, Any], dict[str, Any] | None, list[dict] | None]:
        """Return (chunks, ocr_metadata, docling_json, opd_elements).

        Routing for PDFs is controlled by PDF_EXTRACTOR env setting:
          "opendataloader" (default) — OPD first, docling fallback
          "docling"                  — docling first, OPD fallback
        Non-PDF files (DOCX, etc.) always use docling.
        opd_elements is the raw OPD element list when OPD was used, else None.
        """
        is_pdf = filename.lower().endswith(".pdf")
        opd_primary = settings.pdf_extractor.strip().lower() != "docling"

        if is_pdf and opd_primary:
            chunks, ocr_metadata, extraction_json, opd_elements = await self._try_opd_then_docling(
                filename=filename, content=content
            )
        else:
            # Docling primary (or non-PDF)
            chunks, ocr_metadata, extraction_json, opd_elements = await self._try_docling_then_opd(
                filename=filename, content=content, allow_opd_fallback=is_pdf
            )

        if is_pdf:
            chunks = await asyncio.to_thread(enhance_table_chunks, content, chunks)
            _profile = get_profile_for_document(filename=filename)
            chunks = filter_degenerate_table_chunks(chunks, profile=_profile)

        return chunks, ocr_metadata, extraction_json, opd_elements

    async def _try_opd_then_docling(
        self,
        *,
        filename: str,
        content: bytes,
    ) -> tuple[list[ParsedChunk], dict[str, Any], dict[str, Any] | None, list[dict] | None]:
        if self.opendataloader_adapter is not None:
            try:
                elements = await asyncio.to_thread(
                    self.opendataloader_adapter.convert_bytes,
                    filename=filename,
                    content=content,
                )
                chunks = await asyncio.to_thread(parse_opendataloader_elements, elements)
                content_chunks = [c for c in chunks if c.chunk_type != "heading"]
                if content_chunks:
                    logger.info(
                        "opendataloader extracted filename=%s chunks=%d",
                        filename,
                        len(chunks),
                    )
                    return chunks, {}, {
                        "source": "opendataloader",
                        "element_count": len(elements),
                        "hybrid": bool(self.opendataloader_adapter.hybrid_url),
                    }, elements
                logger.warning(
                    "opendataloader produced no content chunks for %s, falling back to docling",
                    filename,
                )
            except Exception:
                logger.warning(
                    "opendataloader failed for %s, falling back to docling",
                    filename,
                    exc_info=True,
                )

        chunks, ocr_meta, doc_json = await self._run_docling(filename=filename, content=content)
        return chunks, ocr_meta, doc_json, None

    async def _try_docling_then_opd(
        self,
        *,
        filename: str,
        content: bytes,
        allow_opd_fallback: bool,
    ) -> tuple[list[ParsedChunk], dict[str, Any], dict[str, Any] | None, list[dict] | None]:
        chunks, ocr_metadata, doc_json = await self._run_docling(
            filename=filename, content=content
        )
        content_chunks = [c for c in chunks if c.chunk_type != "heading"]
        if content_chunks:
            return chunks, ocr_metadata, doc_json, None

        if allow_opd_fallback and self.opendataloader_adapter is not None:
            logger.warning(
                "docling produced no content chunks for %s, falling back to opendataloader",
                filename,
            )
            try:
                elements = await asyncio.to_thread(
                    self.opendataloader_adapter.convert_bytes,
                    filename=filename,
                    content=content,
                )
                opd_chunks = await asyncio.to_thread(parse_opendataloader_elements, elements)
                if any(c.chunk_type != "heading" for c in opd_chunks):
                    logger.info(
                        "opendataloader fallback extracted filename=%s chunks=%d",
                        filename,
                        len(opd_chunks),
                    )
                    # Save both: docling JSON (from primary attempt) and OPD elements (fallback)
                    return opd_chunks, {}, doc_json, elements
            except Exception:
                logger.warning(
                    "opendataloader fallback also failed for %s",
                    filename,
                    exc_info=True,
                )

        return chunks, ocr_metadata, doc_json, None

    async def _run_docling(
        self,
        *,
        filename: str,
        content: bytes,
    ) -> tuple[list[ParsedChunk], dict[str, Any], dict[str, Any]]:
        dl_doc = await asyncio.to_thread(
            self.docling_adapter.convert_bytes,
            filename=filename,
            content=content,
            auto_ocr=True,
            force_full_page_ocr=False,
            do_table_structure=True,
        )
        doc_json = await asyncio.to_thread(dl_doc.export_to_dict)
        raw_chunks = await asyncio.to_thread(
            chunk_document, dl_doc, merge_list_items=True
        )
        chunks = await asyncio.to_thread(parse_docling_chunks, doc_json, raw_chunks)
        page_count = len(getattr(dl_doc, "pages", {}) or {})
        chunks, ocr_metadata = self._apply_ocr_fallback(
            filename=filename,
            content=content,
            page_count=page_count,
            parsed_chunks=chunks,
        )
        return chunks, ocr_metadata, doc_json

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

        # Detect language from first chunks for better LLM accuracy
        language = await self._detect_language_from_chunks(parsed_chunks)

        enriched = list(parsed_chunks)
        if language:
            for idx, chunk in enumerate(enriched):
                metadata = dict(chunk.metadata)
                metadata.setdefault("detected_language", language)
                enriched[idx] = replace(chunk, metadata=metadata)

        if not clause_texts:
            return enriched

        extracted = await self.llm.extract_policy_meanings(
            texts=clause_texts, language=language
        )
        for chunk_index, meaning in zip(clause_indexes, extracted, strict=False):
            chunk = enriched[chunk_index]
            metadata = dict(chunk.metadata)
            metadata.update(meaning_to_metadata(chunk.text))
            metadata.update({key: str(value or "") for key, value in meaning.items()})
            metadata["semantic_source"] = "llm"
            metadata["detected_language"] = language
            enriched[chunk_index] = replace(chunk, metadata=metadata)
        return enriched

    async def _detect_language_from_chunks(self, chunks: list[ParsedChunk]) -> str:
        """Detect language from first few chunks of text."""
        sample_texts = []
        for chunk in chunks[:5]:
            text = (chunk.text or "").strip()
            if text:
                sample_texts.append(text)
            if len(" ".join(sample_texts)) > 500:
                break
        if not sample_texts:
            return ""
        sample = " ".join(sample_texts)[:500]
        return await self.llm.detect_language(sample)

    def _apply_ocr_fallback(
        self,
        *,
        filename: str,
        content: bytes,
        page_count: int,
        parsed_chunks: list[ParsedChunk],
    ) -> tuple[list[ParsedChunk], dict[str, Any]]:
        if not settings.ocr_fallback_enabled:
            return parsed_chunks, {
                "enabled": False,
                "used": False,
                "reason": "disabled",
            }

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
        # docling_doc: dict[str, Any],
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
        # docling_filename = f"{filename}_{timestamp}.docling.json"

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
        # (target_dir / docling_filename).write_text(
        #     json.dumps(docling_doc, indent=2),
        #     encoding="utf-8",
        # )
        (target_dir / "hierarchy.json").write_text(
            json.dumps(hierarchy, indent=2),
            encoding="utf-8",
        )
