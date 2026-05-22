from __future__ import annotations

import json
import logging
import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import re

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


def _bbox_iou(a: dict, b: dict) -> float:
    """Compute intersection-over-union between two {x0,y0,x1,y1} dicts."""
    ax0, ay0, ax1, ay1 = a.get("x0", 0), a.get("y0", 0), a.get("x1", 0), a.get("y1", 0)
    bx0, by0, bx1, by1 = b.get("x0", 0), b.get("y0", 0), b.get("x1", 0), b.get("y1", 0)
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _candidate_to_table_dict(candidate: Any, *, caption: str = "", section_path: list[str] | None = None) -> dict:
    """Build a structured table dict from a TableCandidate for canonical_table metadata."""
    import uuid
    rows_text: list[list[str]] = []
    cells_by_row: dict[int, list[dict]] = {}
    for cell in candidate.cells:
        r = cell.get("row", 0)
        cells_by_row.setdefault(r, []).append(cell)
    for r in sorted(cells_by_row):
        rows_text.append([str(c.get("text", "")).strip() for c in sorted(cells_by_row[r], key=lambda c: c.get("col", 0))])
    headers = [candidate.headers] if candidate.headers else []
    return {
        "table_uid": str(uuid.uuid4()),
        "caption_original": caption,
        "caption_normalized": caption.lower().strip(),
        "section_path": section_path or [],
        "pages": [candidate.page_number],
        "columns": [{"index": i, "name": h, "normalized": h.lower().strip()} for i, h in enumerate(candidate.headers)],
        "rows": [
            {"row_number": r_idx, "cells": [
                {"row": r_idx, "col": c_idx, "text": text, "is_header": r_idx == 0}
                for c_idx, text in enumerate(row_cells)
            ]}
            for r_idx, row_cells in enumerate(rows_text)
        ],
        "num_rows": candidate.num_rows,
        "num_cols": candidate.num_cols,
        "extraction_backend": candidate.backend_name,
        "confidence": candidate.confidence,
        "headers": candidate.headers,
        "source_extractor": candidate.backend_name,
    }


async def _correlate_camelot_tables(
    pdf_bytes: bytes,
    chunks: list[Any],
    *,
    iou_threshold: float = 0.4,
) -> list[Any]:
    """Run camelot on pages that have docling table chunks and update metadata.

    For each docling table chunk:
    - If a camelot candidate overlaps (IoU > iou_threshold) AND has more cells → use camelot
    - Otherwise keep docling, mark table_source = "docling"

    Requires camelot to be installed (optional dependency). Silently skips if not available.
    """
    import tempfile
    import os
    from grc_policy_server.services.ingestion.backends.camelot_extractor import CamelotTableExtractor

    table_chunks = [c for c in chunks if getattr(c, "chunk_type", "") == "table"]
    if not table_chunks:
        return chunks

    table_pages = sorted({c.page_number for c in table_chunks if c.page_number is not None})
    if not table_pages:
        return chunks

    # Write PDF to temp file — camelot requires a file path
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        extractor = CamelotTableExtractor()
        camelot_candidates = await extractor.extract(tmp_path, page_numbers=table_pages)
    except Exception:
        logger.debug("camelot correlation skipped (extraction failed or not installed)")
        return chunks
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if not camelot_candidates:
        return chunks

    # Index camelot candidates by page
    by_page: dict[int, list[Any]] = {}
    for cand in camelot_candidates:
        by_page.setdefault(cand.page_number, []).append(cand)

    updated = list(chunks)
    for i, chunk in enumerate(updated):
        if getattr(chunk, "chunk_type", "") != "table":
            continue
        page = chunk.page_number
        docling_cells = (chunk.metadata.get("table_structure") or {}).get("num_rows", 0)
        docling_cell_count = docling_cells * ((chunk.metadata.get("table_structure") or {}).get("num_cols", 0) or 1)

        # Docling bbox from bbox_refs
        docling_bbox = None
        bbox_refs = chunk.metadata.get("bbox_refs") or getattr(chunk, "bbox_refs", None) or []
        if bbox_refs:
            r = bbox_refs[0]
            docling_bbox = {"x0": r.get("l", 0), "y0": r.get("b", 0), "x1": r.get("r", 0), "y1": r.get("t", 0)}

        best_cand = None
        best_iou = iou_threshold
        for cand in by_page.get(page, []):
            if docling_bbox:
                iou = _bbox_iou(docling_bbox, cand.bbox)
            else:
                iou = iou_threshold + 0.01  # no bbox → accept any same-page candidate
            if iou >= best_iou:
                cand_cell_count = cand.num_rows * cand.num_cols
                if cand_cell_count >= docling_cell_count:
                    best_iou = iou
                    best_cand = cand

        if best_cand is not None:
            caption = chunk.metadata.get("normalized_caption") or chunk.title or ""
            section_path = list(chunk.section_path or [])
            table_dict = _candidate_to_table_dict(best_cand, caption=caption, section_path=section_path)
            new_meta = {**chunk.metadata, "canonical_table": table_dict, "table_source": best_cand.backend_name}
            from dataclasses import replace as dc_replace
            updated[i] = dc_replace(chunk, metadata=new_meta)
            logger.debug(
                "camelot upgraded table chunk page=%d iou=%.2f rows=%d→%d",
                page, best_iou,
                (chunk.metadata.get("table_structure") or {}).get("num_rows", 0),
                best_cand.num_rows,
            )
        else:
            new_meta = {**chunk.metadata, "table_source": chunk.metadata.get("table_source", "docling")}
            from dataclasses import replace as dc_replace
            updated[i] = dc_replace(chunk, metadata=new_meta)

    upgraded = sum(1 for c in updated if c.chunk_type == "table" and c.metadata.get("table_source") not in ("docling", None))
    if upgraded:
        logger.info("camelot correlation upgraded %d/%d table chunks", upgraded, len(table_chunks))
    return updated

_LANG_LEXICONS: dict[str, set[str]] = {
    "en": {"the", "and", "shall", "must", "should", "policy", "document", "requirements",
           "control", "controls", "access", "security", "is", "are"},
    "de": {"der", "die", "das", "und", "nicht", "mit", "sind", "muss", "müssen",
           "soll", "sollen", "richtlinie", "dokument", "anforderungen", "zugriff", "sicherheit"},
    "fr": {"le", "la", "les", "et", "pas", "avec", "sont", "doit", "doivent",
           "politique", "document", "exigences", "accès", "securite", "sécurité", "conformité"},
}


def _detect_language_rule_based(chunks: list[ParsedChunk]) -> str:
    """Rule-based language detection from chunk text — no LLM required."""
    sample_texts = []
    for chunk in chunks[:5]:
        text = (chunk.text or "").strip()
        if text:
            sample_texts.append(text)
        if len(" ".join(sample_texts)) > 500:
            break
    if not sample_texts:
        return ""
    sample = " ".join(sample_texts)[:500].lower()
    tokens = re.findall(r"[a-zA-ZÀ-ÿ]+", sample)
    if not tokens:
        return ""
    scores: dict[str, int] = {code: 0 for code in _LANG_LEXICONS}
    for token in tokens:
        for code, lexicon in _LANG_LEXICONS.items():
            if token in lexicon:
                scores[code] += 1
    if any(ch in sample for ch in "äöüß"):
        scores["de"] += 2
    if any(ch in sample for ch in "àâçéèêëîïôûùüÿœæ"):
        scores["fr"] += 2
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return ""
    winners = [code for code, score in scores.items() if score == scores[best]]
    return best if len(winners) == 1 else ""


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
        weaviate: WeaviateClient | None,
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
        docling_language = str(ocr_metadata.pop("_docling_language", "") or "")
        self._log_extraction_score(filename, parsed_chunks)
        parsed_chunks = preprocess_parsed_chunks(parsed_chunks)
        parsed_chunks = await self._enrich_clause_semantics(parsed_chunks, docling_language=docling_language)

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

        if self.weaviate is not None:
            try:
                self.weaviate.upsert_chunks(vector_records)
            except Exception:
                logger.warning(
                    "weaviate upsert failed for document_id=%s filename=%s — "
                    "canonical nodes already saved, upload will succeed",
                    document_id,
                    filename,
                    exc_info=True,
                )
        if self.neo4j is not None:
            try:
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
            except Exception:
                logger.warning(
                    "neo4j upsert failed for document_id=%s filename=%s — "
                    "canonical nodes already saved, upload will succeed",
                    document_id,
                    filename,
                    exc_info=True,
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
            chunks = await _correlate_camelot_tables(content, chunks)

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
        if settings.docling_vlm_enabled and filename.lower().endswith(".pdf"):
            dl_doc = await asyncio.to_thread(
                self.docling_adapter.convert_bytes_vlm,
                filename=filename,
                content=content,
            )
        else:
            dl_doc = await asyncio.to_thread(
                self.docling_adapter.convert_bytes,
                filename=filename,
                content=content,
                auto_ocr=True,
                force_full_page_ocr=False,
                do_table_structure=True,
            )
        dl_doc = await self._apply_targeted_table_ocr(
            filename=filename, content=content, dl_doc=dl_doc
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
        # Use Docling's own language detection when available (docling >= 1.8)
        docling_langs = getattr(getattr(dl_doc, "meta", None), "languages", None) or []
        if docling_langs:
            ocr_metadata["_docling_language"] = str(docling_langs[0]).lower()[:2]
        return chunks, ocr_metadata, doc_json

    async def _apply_targeted_table_ocr(
        self,
        *,
        filename: str,
        content: bytes,
        dl_doc: Any,
    ) -> Any:
        """Re-run Docling with full-page OCR on pages containing low-density tables.

        Disabled by default (DOCLING_TABLE_OCR_ENABLED=false). When enabled,
        tables with fewer than docling_table_ocr_min_density fraction of non-empty
        cells trigger a second pass with force_full_page_ocr=True on the surrounding
        page range. Improved cells are merged back into the original document.
        """
        if not settings.docling_table_ocr_enabled:
            return dl_doc

        try:
            tables = list(getattr(dl_doc, "tables", None) or [])
            if not tables:
                return dl_doc

            margin = settings.docling_table_ocr_page_margin
            min_density = settings.docling_table_ocr_min_density

            # Collect page numbers of low-density tables
            page_numbers: list[int] = []
            for table in tables:
                if self.docling_adapter._table_cell_density(table) < min_density:
                    try:
                        page_no = int(table.prov[0].page_no)
                        page_numbers.append(page_no)
                    except Exception:
                        pass

            if not page_numbers:
                return dl_doc

            # Union page ranges with margin
            total_pages = len(getattr(dl_doc, "pages", {}) or {}) or 9999
            ranges: list[tuple[int, int]] = []
            for page_no in sorted(set(page_numbers)):
                lo = max(1, page_no - margin)
                hi = min(total_pages, page_no + margin)
                if ranges and lo <= ranges[-1][1] + 1:
                    ranges[-1] = (ranges[-1][0], max(ranges[-1][1], hi))
                else:
                    ranges.append((lo, hi))

            # Re-run OCR for each page range and merge improved tables
            for rng in ranges:
                re_doc = await asyncio.to_thread(
                    self.docling_adapter.convert_bytes_page_range,
                    filename=filename,
                    content=content,
                    page_range=rng,
                )
                if re_doc is None:
                    continue
                re_tables = list(getattr(re_doc, "tables", None) or [])
                for orig_table in tables:
                    try:
                        orig_page = int(orig_table.prov[0].page_no)
                        orig_cols = len(orig_table.data.grid[0]) if orig_table.data.grid else 0
                    except Exception:
                        continue
                    if not (rng[0] <= orig_page <= rng[1]):
                        continue
                    for re_table in re_tables:
                        try:
                            re_page = int(re_table.prov[0].page_no)
                            re_cols = len(re_table.data.grid[0]) if re_table.data.grid else 0
                        except Exception:
                            continue
                        if re_page == orig_page and re_cols == orig_cols:
                            orig_table.data.grid = re_table.data.grid
                            break

            logger.info(
                "targeted table OCR applied filename=%s low_density_pages=%s ranges=%s",
                filename,
                page_numbers,
                ranges,
            )
        except Exception:
            logger.exception("targeted table OCR failed filename=%s; using original", filename)

        return dl_doc

    async def _enrich_clause_semantics(
        self,
        parsed_chunks: list[ParsedChunk],
        docling_language: str = "",
    ) -> list[ParsedChunk]:
        language = docling_language or _detect_language_rule_based(parsed_chunks)

        enriched = list(parsed_chunks)
        for index, chunk in enumerate(enriched):
            if language:
                metadata = dict(chunk.metadata)
                metadata.setdefault("detected_language", language)
                enriched[index] = replace(chunk, metadata=metadata)

            if chunk.chunk_type != "clause":
                continue
            text = (chunk.text or "").strip()
            if not text:
                continue

            metadata = dict(chunk.metadata)
            metadata.update(meaning_to_metadata(text))
            metadata["semantic_source"] = "rule_based"
            if language:
                metadata["detected_language"] = language
            enriched[index] = replace(chunk, metadata=metadata)

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
