from __future__ import annotations

import json
import logging
import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from grc_policy_server.core.config import settings
from grc_policy_server.services.documents.canonical_store import CanonicalDocumentStore
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.graph.docling_graph_adapter import (
    DoclingGraphAdapter,
    DoclingGraphArtifact,
)
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
from grc_policy_server.services.ingestion.chunk_enricher import ChunkEnricher
from grc_policy_server.services.ingestion.document_family_profile import get_profile_for_document
from grc_policy_server.services.ingestion.table_quality_enhancer import (
    enhance_table_chunks,
    filter_degenerate_table_chunks,
)
from grc_policy_server.services.ingestion.policy_preprocessor import (
    preprocess_parsed_chunks,
)
from grc_policy_server.services.ingestion.standards.standard_registry import (
    StandardRegistry,
    StandardResolution,
)
from grc_policy_server.services.llm.base import BaseLLM
from grc_policy_server.services.orchestration.ingestion_orchestrator import (
    ComplianceIngestionOrchestrator,
    DeterministicFailure,
    IngestionToolRegistry,
)
from grc_policy_server.services.orchestration.job_state import JobState
from grc_policy_server.services.validation.evidence_chain_validator import (
    EvidenceChainValidator,
    MIN_COMPLIANCE_NODES,
)
from grc_policy_server.services.vector.qdrant_store import QdrantVectorClient
from grc_policy_server.utils.hashing import sha256_hex, stable_uuid

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


def _score_table_extraction_quality(
    cells: list[dict],
    num_rows: int,
    num_cols: int,
    headers: list[str],
) -> float:
    """Score table extraction quality from 0.0 (empty) to 1.0 (perfect).

    Weights: 40% cell fill, 30% numeric density, 20% header quality, 10% dimensions.
    Used to choose between Docling and Camelot extractions rather than relying on
    raw cell count alone (which favours empty Docling grids over richer Camelot output).
    """
    total_cells = num_rows * num_cols
    if total_cells == 0:
        return 0.0
    non_empty = sum(1 for c in cells if str(c.get("text", "")).strip())
    numeric_cells = sum(
        1 for c in cells if any(ch.isdigit() for ch in str(c.get("text", "")))
    )
    fill_score = non_empty / total_cells
    numeric_density = numeric_cells / max(1, non_empty) if non_empty else 0.0
    header_quality = (
        0.0
        if not headers
        else sum(1 for h in headers if h and not h.startswith("column_")) / len(headers)
    )
    dimension_score = 1.0 if 2 <= num_rows <= 200 and 2 <= num_cols <= 20 else 0.5
    return (
        0.40 * fill_score
        + 0.30 * numeric_density
        + 0.20 * header_quality
        + 0.10 * dimension_score
    )


def _table_quality_flags(
    cells: list[dict],
    num_rows: int,
    num_cols: int,
    headers: list[str],
) -> list[str]:
    """Return deterministic quality flags for downstream comparison.

    These flags do not suppress tables.  They tell the matcher not to trust
    schema signatures produced from sparse grids or placeholder row-label
    headers, which are common in image-heavy standards pages.
    """
    flags: list[str] = []
    total_cells = max(0, int(num_rows or 0)) * max(0, int(num_cols or 0))
    non_empty = sum(1 for c in cells if str(c.get("text", "")).strip())
    fill_rate = non_empty / total_cells if total_cells else 0.0
    placeholder_headers = [
        h for h in headers
        if str(h).startswith("column_") or str(h).startswith("_row_label_")
    ]

    if not cells or total_cells == 0:
        flags.append("missing_cells")
    elif fill_rate < 0.50:
        flags.append("sparse_cells")
    if headers and placeholder_headers:
        flags.append("placeholder_headers")
    if headers and len(placeholder_headers) >= max(1, len(headers) // 2):
        flags.append("mostly_placeholder_headers")
    if num_rows < 2 or num_cols < 2:
        flags.append("minimal_dimensions")

    return flags


def _is_sparse_placeholder_table_metadata(metadata: dict[str, Any]) -> bool:
    flags = {str(flag) for flag in (metadata.get("table_quality_flags") or [])}
    if {"sparse_cells", "placeholder_headers"} <= flags:
        return True
    headers = list(metadata.get("table_headers") or [])
    structure = metadata.get("table_structure") or {}
    cells = list(structure.get("cells") or [])
    rows = int(structure.get("num_rows") or 0)
    cols = int(structure.get("num_cols") or 0)
    total = rows * cols
    fill = (
        sum(1 for cell in cells if str(cell.get("text") or "").strip()) / total
        if total
        else 0.0
    )
    has_placeholder = any(
        str(header).startswith("column_")
        or str(header).startswith("_row_label_")
        for header in headers
    )
    return has_placeholder and fill < 0.50


def _score_cell_confidence(cell_data: dict, column_role: str | None) -> "CellConfidence":
    """Score confidence for individual cell extraction (0.0–1.0).

    Factors:
    - cell_fill: 1.0 if text present, 0.0 if empty
    - cell_type_match: 1.0 if type matches role (numeric for limit/result, etc.)
    - unit_validity: 1.0 if unit present and recognized (for limit columns)
    - ocr_confidence: OCR confidence if OCR was used, else 1.0 (native text)

    Weights: 0.30 * fill + 0.35 * type_match + 0.20 * unit + 0.15 * ocr
    """
    import re
    from grc_policy_server.models.schemas import CellConfidence

    text = str(cell_data.get("text") or "").strip()
    flags = []

    # Factor 1: cell_fill
    cell_fill = 1.0 if text else 0.0

    # Factor 2: cell_type_match (by column_role)
    cell_type_match = 1.0
    if column_role in ("limit", "result"):
        # Expect numeric value
        if text:
            numeric_match = bool(re.search(r'\d+[\.,]?\d*', text))
            cell_type_match = 0.9 if numeric_match else 0.5
            if not numeric_match:
                flags.append("unparseable_number")
        else:
            cell_type_match = 0.0
            flags.append("missing_limit_value")
    elif column_role == "condition":
        # Expect text with semantic signal
        cell_type_match = 0.95 if text else 0.0
    elif column_role == "row_key":
        cell_type_match = 1.0 if text else 0.3

    # Factor 3: unit_validity (for limit columns)
    unit_validity = 1.0
    if column_role == "limit" and text:
        # Check if unit-like pattern exists
        unit_match = bool(re.search(r'(dB\w+|[MkGT]?Hz|[muMnp]?[VACWΩ]|%|°C)', text))
        unit_validity = 0.95 if unit_match else 0.7
        if not unit_match:
            flags.append("missing_unit")

    # Factor 4: ocr_confidence
    ocr_confidence = cell_data.get("ocr_confidence", 1.0) if cell_data.get("ocr_used") else 1.0

    # Weighted score
    confidence = (
        0.30 * cell_fill
        + 0.35 * cell_type_match
        + 0.20 * unit_validity
        + 0.15 * ocr_confidence
    )

    return CellConfidence(
        chunk_id=cell_data.get("chunk_id", ""),
        column_name=cell_data.get("column_name"),
        column_role=column_role,
        confidence=max(0.0, min(1.0, confidence)),
        confidence_factors={
            "cell_fill": cell_fill,
            "cell_type_match": cell_type_match,
            "unit_validity": unit_validity,
            "ocr_confidence": ocr_confidence,
        },
        flags=flags if flags else None,
    )


def _score_row_confidence(
    row_data: dict,
    table_confidence: float,
    cell_confidences: list["CellConfidence"],
    footnote_scope_confidence: float = 1.0,
) -> "RequirementConfidence":
    """Score confidence for row/requirement extraction.

    Row confidence = min(key cell confidences, table confidence, footnote scope).
    Flags for review if any key cell confidence < 0.70 or missing key cells.
    """
    from grc_policy_server.models.schemas import RequirementConfidence

    key_cell_confidences = {
        cc.column_name or cc.column_role: cc.confidence
        for cc in cell_confidences
        if cc.column_role in ("limit", "result", "condition", "row_key")
    }

    if not key_cell_confidences:
        row_confidence = table_confidence * 0.5
        review_reason = "missing_key_cells"
    else:
        row_confidence = min(key_cell_confidences.values())

    # Apply table and footnote modifiers
    row_confidence = min(row_confidence, table_confidence, footnote_scope_confidence)

    return RequirementConfidence(
        row_id=row_data.get("row_id", ""),
        row_key=row_data.get("row_key"),
        confidence=row_confidence,
        key_cell_confidences=key_cell_confidences,
        applicability_confidence=footnote_scope_confidence,
        requires_review=row_confidence < 0.70,
        review_reason="low_key_cell_confidence" if row_confidence < 0.70 else None,
    )


def _merge_table_extractions(docling_chunk: Any, camelot_cand: Any) -> dict:
    """Merge cell data from Docling and Camelot when quality scores are tied.

    Strategy: keep Docling's section hierarchy / title context; for each (row, col)
    position take the non-empty cell text from whichever backend has it, preferring
    Camelot when both have content (Camelot grid extraction is usually cleaner).

    Returns a canonical_table dict suitable for chunk.metadata["canonical_table"].
    """
    import uuid

    docling_cells_raw = (docling_chunk.metadata.get("table_structure") or {}).get("cells") or []
    camelot_cells_raw = list(camelot_cand.cells)

    # Index both by (row, col)
    docling_by_pos: dict[tuple[int, int], str] = {
        (int(c.get("row", 0)), int(c.get("col", 0))): str(c.get("text", "")).strip()
        for c in docling_cells_raw
    }
    camelot_by_pos: dict[tuple[int, int], str] = {
        (int(c.get("row", 0)), int(c.get("col", 0))): str(c.get("text", "")).strip()
        for c in camelot_cells_raw
    }

    # Union of positions
    all_positions = set(docling_by_pos) | set(camelot_by_pos)
    merged_cells = []
    for row, col in sorted(all_positions):
        camelot_text = camelot_by_pos.get((row, col), "")
        docling_text = docling_by_pos.get((row, col), "")
        text = camelot_text if camelot_text else docling_text
        merged_cells.append({"row": row, "col": col, "text": text, "is_header": row == 0})

    nr = camelot_cand.num_rows
    nc = camelot_cand.num_cols
    caption = docling_chunk.metadata.get("normalized_caption") or docling_chunk.title or ""
    section_path = list(docling_chunk.section_path or [])

    return {
        "table_uid": str(uuid.uuid4()),
        "caption_original": caption,
        "caption_normalized": caption.lower().strip(),
        "section_path": section_path,
        "pages": [camelot_cand.page_number],
        "columns": [
            {"index": i, "name": h, "normalized": h.lower().strip()}
            for i, h in enumerate(camelot_cand.headers)
        ],
        "rows": [],
        "num_rows": nr,
        "num_cols": nc,
        "extraction_backend": "ensemble",
        "confidence": (camelot_cand.confidence + 0.80) / 2,
        "headers": camelot_cand.headers,
        "source_extractor": "ensemble",
        "merged_cells": merged_cells,
    }


async def _correlate_camelot_tables(
    pdf_bytes: bytes,
    chunks: list[Any],
    *,
    iou_threshold: float = 0.4,
) -> list[Any]:
    """Run camelot on pages that have docling table chunks and update metadata.

    Selection strategy (quality-score based, not raw cell count):
    - If camelot quality score > docling quality + 0.05 → use Camelot
    - If scores are within 0.05 and IoU ≥ 0.25 → merge both (ensemble)
    - Otherwise keep Docling

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

        # Docling bbox from bbox_refs
        docling_bbox = None
        bbox_refs = chunk.metadata.get("bbox_refs") or getattr(chunk, "bbox_refs", None) or []
        if bbox_refs:
            r = bbox_refs[0]
            docling_bbox = {"x0": r.get("l", 0), "y0": r.get("b", 0), "x1": r.get("r", 0), "y1": r.get("t", 0)}

        # Compute Docling extraction quality
        docling_ts = chunk.metadata.get("table_structure") or {}
        docling_struct_cells = docling_ts.get("cells") or []
        docling_nr = docling_ts.get("num_rows") or 0
        docling_nc = docling_ts.get("num_cols") or 0
        docling_headers = chunk.metadata.get("table_headers") or []
        docling_quality = _score_table_extraction_quality(
            docling_struct_cells, docling_nr, docling_nc, docling_headers
        )

        # Find best Camelot candidate (lowest IoU threshold for quality evaluation).
        # Sparse placeholder-header tables are often bad Docling bbox/table-structure
        # captures, so allow a same-page rescue candidate even when bbox overlap is
        # weak. Quality still has to win before the replacement is accepted.
        _EVAL_IOU = 0.25   # threshold for quality comparison / merge
        sparse_rescue = _is_sparse_placeholder_table_metadata(chunk.metadata)
        best_cand = None
        best_iou = _EVAL_IOU
        best_quality = 0.0
        for cand in by_page.get(page, []):
            if docling_bbox:
                iou = _bbox_iou(docling_bbox, cand.bbox)
            else:
                iou = _EVAL_IOU + 0.01  # no bbox → accept any same-page candidate
            accepted = iou >= _EVAL_IOU or sparse_rescue
            if accepted:
                cq = _score_table_extraction_quality(
                    list(cand.cells), cand.num_rows, cand.num_cols, cand.headers
                )
                if cq > best_quality or (cq == best_quality and iou > best_iou):
                    best_quality = cq
                    best_iou = iou
                    best_cand = cand

        from dataclasses import replace as dc_replace
        if best_cand is not None:
            caption = chunk.metadata.get("normalized_caption") or chunk.title or ""
            section_path = list(chunk.section_path or [])
            quality_gap = best_quality - docling_quality

            if quality_gap > 0.05:
                # Camelot clearly better → use Camelot exclusively
                table_dict = _candidate_to_table_dict(
                    best_cand, caption=caption, section_path=section_path
                )
                new_meta = {
                    **chunk.metadata,
                    "canonical_table": table_dict,
                    "table_source": best_cand.backend_name,
                    "extraction_quality_score": round(best_quality, 3),
                    "table_rescue_reason": "sparse_placeholder_camelot"
                    if sparse_rescue
                    else "",
                }
                updated[i] = dc_replace(chunk, metadata=new_meta)
                logger.debug(
                    "camelot won table page=%d iou=%.2f quality %.2f→%.2f sparse_rescue=%s",
                    page, best_iou, docling_quality, best_quality, sparse_rescue,
                )
            elif abs(quality_gap) <= 0.05 and best_iou >= _EVAL_IOU:
                # Tied → ensemble merge
                table_dict = _merge_table_extractions(chunk, best_cand)
                new_meta = {
                    **chunk.metadata,
                    "canonical_table": table_dict,
                    "table_source": "ensemble",
                    "extraction_quality_score": round(
                        (docling_quality + best_quality) / 2, 3
                    ),
                }
                updated[i] = dc_replace(chunk, metadata=new_meta)
                logger.debug(
                    "ensemble merged table page=%d iou=%.2f docling=%.2f camelot=%.2f",
                    page, best_iou, docling_quality, best_quality,
                )
            else:
                # Docling better → keep Docling, still record quality
                new_meta = {
                    **chunk.metadata,
                    "table_source": "docling",
                    "extraction_quality_score": round(docling_quality, 3),
                }
                updated[i] = dc_replace(chunk, metadata=new_meta)
        else:
            new_meta = {
                **chunk.metadata,
                "table_source": chunk.metadata.get("table_source", "docling"),
                "extraction_quality_score": round(docling_quality, 3),
            }
            from dataclasses import replace as dc_replace
            updated[i] = dc_replace(chunk, metadata=new_meta)

    upgraded = sum(1 for c in updated if c.chunk_type == "table" and c.metadata.get("table_source") not in ("docling", None))
    if upgraded:
        logger.info("camelot correlation upgraded %d/%d table chunks", upgraded, len(table_chunks))
    return updated



def _apply_ontology_to_chunks(
    chunks: list[ParsedChunk],
    classifications: list,
) -> list[ParsedChunk]:
    """Return a new list of ParsedChunks with ontology_type/confidence in metadata.

    Uses dataclasses.replace() because ParsedChunk is frozen.
    """
    result = []
    for chunk, cls in zip(chunks, classifications):
        new_meta = {
            **chunk.metadata,
            "ontology_type": cls.ontology_type,
            "ontology_confidence": cls.confidence,
        }
        result.append(replace(chunk, metadata=new_meta))
    # Preserve any trailing chunks if lists are unequal length (defensive).
    result.extend(chunks[len(classifications):])
    return result


@dataclass(frozen=True)
class UploadIngestionResult:
    """Identifiers returned after a document is successfully ingested."""

    document_id: str
    chunks_stored: int


@dataclass
class _IngestionContext:
    filename: str
    content: bytes
    content_type: str | None
    document_id: str
    content_hash: str
    parsed_chunks: list[ParsedChunk] = field(default_factory=list)
    ocr_metadata: dict[str, Any] = field(default_factory=dict)
    doc_json: dict[str, Any] | None = None
    docling_language: str = ""
    normalized_tree: dict[str, Any] = field(default_factory=dict)
    vector_records: list[dict[str, Any]] = field(default_factory=list)
    docling_graph: DoclingGraphArtifact | None = None
    chunks_stored: int = 0
    resolved_standards: dict[str, StandardResolution] = field(default_factory=dict)


class DocumentIngestionService:
    """Converts uploaded files into chunks and stores metadata/index entries."""

    def __init__(
        self,
        *,
        docling_adapter: DoclingAdapter,
        qdrant: QdrantVectorClient | None,
        neo4j: Neo4jClient | None,
        llm: BaseLLM,
        upload_root: Path,
        canonical_store: CanonicalDocumentStore | None = None,
        ontology_classifier=None,   # OntologyClassifier | None
        human_review_queue=None,    # HumanReviewQueue | None
        audit_log=None,             # AuditLogStore | None
    ):
        self.docling_adapter = docling_adapter
        self.qdrant = qdrant
        self.neo4j = neo4j
        self.llm = llm
        self.upload_root = upload_root
        self.canonical_store = canonical_store
        self._ontology_classifier = ontology_classifier
        self._human_review_queue = human_review_queue
        self._audit_log = audit_log

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
        context = _IngestionContext(
            filename=filename,
            content=content,
            content_type=content_type,
            document_id=document_id,
            content_hash=content_hash,
        )
        registry = self._build_ingestion_tool_registry()
        orchestrator = ComplianceIngestionOrchestrator(
            tool_registry=registry,
            audit_log=self._audit_log,
        )
        try:
            _, context = await orchestrator.run(
                job=JobState(job_id=f"ingest-{document_id}", doc_id=document_id),
                context=context,
            )
        except DeterministicFailure as exc:
            if isinstance(exc.__cause__, ValueError):
                raise exc.__cause__ from exc
            raise

        logger.info(
            "ingested upload document_id=%s filename=%s indexed=%s",
            document_id,
            filename,
            context.chunks_stored,
        )
        return UploadIngestionResult(
            document_id=document_id,
            chunks_stored=context.chunks_stored,
        )

    def _build_ingestion_tool_registry(self) -> IngestionToolRegistry:
        registry = IngestionToolRegistry()
        registry.register("quality_gate", self._stage_quality_gate)
        registry.register("parse", self._stage_parse)
        registry.register("extract_docling_graph", self._stage_extract_docling_graph)
        registry.register("canonicalize", self._stage_canonicalize)
        registry.register("normalize_tables", self._stage_normalize_tables)
        registry.register("resolve_standards", self._stage_resolve_standards)
        registry.register("map_ontology", self._stage_map_ontology)
        registry.register("resolve_stable_identities", self._stage_resolve_stable_identities)
        registry.register("build_graph", self._stage_build_graph)
        registry.register("build_relationships", self._stage_build_relationships)
        registry.register("validate_evidence_chains", self._stage_validate_evidence_chains)
        registry.register("embed_nodes", self._stage_embed_nodes)
        registry.register("mark_ready", self._stage_mark_ready)
        return registry

    async def _stage_quality_gate(self, context: _IngestionContext) -> _IngestionContext:
        if not context.filename:
            raise ValueError("Missing upload filename")
        if not context.content:
            raise ValueError("Uploaded file is empty")
        return context

    async def _stage_parse(self, context: _IngestionContext) -> _IngestionContext:
        chunks, ocr_metadata, doc_json = await self._extract_parsed_chunks(
            filename=context.filename,
            content=context.content,
        )
        context.parsed_chunks = chunks
        context.ocr_metadata = ocr_metadata
        context.doc_json = doc_json
        context.docling_language = str(context.ocr_metadata.pop("_docling_language", "") or "")
        return context

    async def _stage_extract_docling_graph(self, context: _IngestionContext) -> _IngestionContext:
        self._log_extraction_score(context.filename, context.parsed_chunks)
        return context

    async def _stage_canonicalize(self, context: _IngestionContext) -> _IngestionContext:
        chunks = preprocess_parsed_chunks(context.parsed_chunks)
        context.parsed_chunks = ChunkEnricher().enrich(
            chunks,
            docling_language=context.docling_language,
        )
        return context

    async def _stage_normalize_tables(self, context: _IngestionContext) -> _IngestionContext:
        from dataclasses import replace as dc_replace
        from grc_policy_server.services.ingestion.ontology.emc_ontology import EMCTestClassifier

        classifier = EMCTestClassifier()
        updated: list[ParsedChunk] = []
        for chunk in context.parsed_chunks:
            if chunk.chunk_type != "table":
                updated.append(chunk)
                continue
            headers = list(chunk.metadata.get("table_headers") or [])
            caption = str(chunk.metadata.get("normalized_caption") or chunk.title or "")
            section_path = list(chunk.section_path or [])
            table_type = classifier.classify_table(caption, headers).value
            if table_type == "unknown":
                table_type = classifier.classify_from_section_path(section_path).value
            quality = float(chunk.metadata.get("extraction_quality_score") or 0.0)
            structure = chunk.metadata.get("table_structure") or {}
            cells = list(structure.get("cells") or [])
            num_rows = int(structure.get("num_rows") or 0)
            num_cols = int(structure.get("num_cols") or 0)
            flags = _table_quality_flags(cells, num_rows, num_cols, headers)
            if quality <= 0.0:
                quality = _score_table_extraction_quality(
                    cells,
                    num_rows,
                    num_cols,
                    headers,
                )
            new_meta = {
                **chunk.metadata,
                "table_type": table_type,
                "extraction_quality_score": round(quality, 3),
                "table_quality_flags": flags,
                "low_confidence_table": bool(flags),
            }
            updated.append(dc_replace(chunk, metadata=new_meta))
        context.parsed_chunks = updated
        return context

    async def _stage_resolve_standards(self, context: _IngestionContext) -> _IngestionContext:
        registry = StandardRegistry()
        resolved: dict[str, StandardResolution] = {}
        for chunk in context.parsed_chunks:
            std_ref = str(chunk.metadata.get("standard_ref") or "").strip()
            if std_ref and std_ref not in resolved:
                resolved[std_ref] = registry.resolve(std_ref)
            # Also scan text for inline standard references
            text = (chunk.text or "")[:500]
            for match in __import__("re").finditer(
                r"\b(CISPR\s*\d+|IEC\s*6\d{4}[-\s\d]*|FCC\s*Part\s*\d+|ISO\s*[\d/]+|EN\s*\d+)\b",
                text,
                __import__("re").IGNORECASE,
            ):
                ref = match.group(0).strip()
                if ref not in resolved:
                    resolved[ref] = registry.resolve(ref)
        context.resolved_standards = resolved
        logger.debug("resolved %d standard refs for document_id=%s", len(resolved), context.document_id)
        return context

    async def _stage_resolve_stable_identities(self, context: _IngestionContext) -> _IngestionContext:
        from dataclasses import replace as dc_replace
        import re as _re

        _clause_re = _re.compile(
            r"^\s*((?:section|clause|article|appendix|annex)?\s*[A-Za-z]?\d+(?:\.\d+)*[A-Za-z]?)\b",
            _re.IGNORECASE,
        )
        updated: list[ParsedChunk] = []
        for chunk in context.parsed_chunks:
            otype = chunk.metadata.get("ontology_type") or ""
            std_ref = str(chunk.metadata.get("standard_ref") or "").strip()
            # Priority 1: standard_id + clause gives the most stable ID
            if otype in {"Requirement", "Measurement"} and std_ref:
                clause_match = _clause_re.search(chunk.title or chunk.metadata.get("section_path") or "")
                if clause_match:
                    clause = clause_match.group(1).strip()
                    resolution = context.resolved_standards.get(std_ref)
                    std_id = getattr(resolution, "standard_id", None) or std_ref.lower().replace(" ", "_")
                    new_stable_id = stable_uuid(f"section::{std_id}::{clause}")
                    new_meta = {**chunk.metadata, "stable_id": new_stable_id, "stable_id_basis": "standard_clause"}
                    updated.append(dc_replace(chunk, metadata=new_meta))
                    continue
            updated.append(chunk)
        context.parsed_chunks = updated
        return context

    async def _stage_map_ontology(self, context: _IngestionContext) -> _IngestionContext:
        context.parsed_chunks = await self._run_ontology_classification(
            document_id=context.document_id,
            chunks=context.parsed_chunks,
        )
        return context

    async def _stage_build_graph(self, context: _IngestionContext) -> _IngestionContext:
        hierarchy = build_document_hierarchy(
            document_id=context.document_id,
            filename=context.filename,
            parsed_chunks=context.parsed_chunks,
            content_hash=context.content_hash,
        )
        context.normalized_tree = {
            "documentStableId": hierarchy.document_stable_id,
            "documentFamily": hierarchy.document_family,
            "contentHash": hierarchy.content_hash,
            "metadata": {
                **hierarchy.metadata,
                "ocr": context.ocr_metadata,
                "content_type": context.content_type,
            },
            "nodes": [node.to_graph_record() for node in hierarchy.nodes],
        }
        context.vector_records = [
            node.to_vector_record() for node in hierarchy.indexable_nodes
        ]
        if not context.vector_records:
            raise ValueError("No indexable text nodes produced from uploaded document")
        context.docling_graph = DoclingGraphAdapter().build_artifact(
            document_id=context.document_id,
            filename=context.filename,
            document_stable_id=hierarchy.document_stable_id,
            document_family=hierarchy.document_family,
            content_hash=context.content_hash,
            nodes=context.normalized_tree["nodes"],
            metadata=context.normalized_tree["metadata"],
            resolved_standards=context.resolved_standards,
        )
        context.normalized_tree["metadata"]["ignored_changes"] = list(
            context.docling_graph.ignored_nodes
        )
        if self.canonical_store is not None:
            self.canonical_store.save_document(
                document_id=context.document_id,
                filename=context.filename,
                content_hash=context.content_hash,
                docling_json=context.doc_json,
                hierarchy=context.normalized_tree,
                metadata=context.normalized_tree["metadata"],
            )
        return context

    async def _stage_build_relationships(self, context: _IngestionContext) -> _IngestionContext:
        if self.neo4j is not None:
            try:
                self.neo4j.upsert_document_hierarchy(
                    document_id=context.document_id,
                    filename=context.filename,
                    document_stable_id=context.normalized_tree.get("documentStableId"),
                    document_family=context.normalized_tree.get("documentFamily"),
                    content_hash=context.content_hash,
                    nodes=context.normalized_tree.get("nodes", []),
                    metadata=context.normalized_tree.get("metadata", {}),
                )
                if context.docling_graph is not None:
                    self.neo4j.upsert_docling_graph(context.docling_graph)
            except Exception as exc:
                logger.warning(
                    "neo4j upsert failed for document_id=%s filename=%s — "
                    "canonical nodes already saved, upload will succeed error_type=%s error=%s",
                    context.document_id,
                    context.filename,
                    type(exc).__name__,
                    str(exc),
                )
        return context

    async def _stage_validate_evidence_chains(self, context: _IngestionContext) -> _IngestionContext:
        if context.docling_graph is None:
            return context
        compliance_count = sum(1 for n in context.docling_graph.nodes if n.layer == "compliance")
        if compliance_count < MIN_COMPLIANCE_NODES:
            raise DeterministicFailure(
                f"graph has only {compliance_count} compliance nodes (minimum {MIN_COMPLIANCE_NODES}) "
                "— document likely unparseable or entirely excluded from compliance index"
            )
        report = EvidenceChainValidator().validate(context.docling_graph)
        logger.info(
            "evidence_chain document_id=%s status=%s coverage=%.1f%% incomplete=%d",
            context.document_id,
            report.chain_status,
            report.coverage_pct * 100,
            len(report.incomplete_node_ids),
        )
        if report.review_required:
            # Route to review but do not halt (ARCHITECTURE.md: agent failures continue)
            context.normalized_tree.setdefault("metadata", {})["evidence_chain_status"] = report.chain_status
            context.normalized_tree["metadata"]["evidence_chain_coverage"] = report.coverage_pct
        return context

    async def _stage_embed_nodes(self, context: _IngestionContext) -> _IngestionContext:
        if self.qdrant is not None:
            try:
                self.qdrant.upsert_chunks(context.vector_records)
            except Exception:
                logger.warning(
                    "qdrant upsert failed for document_id=%s filename=%s — "
                    "canonical nodes already saved, upload will succeed",
                    context.document_id,
                    context.filename,
                    exc_info=True,
                )
        context.chunks_stored = len(context.vector_records)
        return context

    async def _stage_mark_ready(self, context: _IngestionContext) -> _IngestionContext:
        self._persist_upload_metadata(
            document_id=context.document_id,
            filename=context.filename,
            content=context.content,
            content_type=context.content_type,
            hierarchy=context.normalized_tree,
            ocr_metadata=context.ocr_metadata,
            chunks_stored=context.chunks_stored,
            docling_graph=context.docling_graph,
        )
        return context

    async def _stage_noop(self, context: _IngestionContext) -> _IngestionContext:
        return context

    async def _run_ontology_classification(
        self,
        *,
        document_id: str,
        chunks: list[ParsedChunk],
    ) -> list[ParsedChunk]:
        """Classify chunks into the 10-type universal ontology (opt-in, async only).

        Skipped when `settings.ontology_classification_enabled` is False or no
        classifier is configured.  Errors per-chunk fall back to type="Section".
        """
        if not settings.ontology_classification_enabled:
            return chunks
        if self._ontology_classifier is None:
            return chunks
        try:
            classifications = await self._ontology_classifier.classify_batch(chunks)
            chunks = _apply_ontology_to_chunks(chunks, classifications)
            if self._human_review_queue is not None:
                self._human_review_queue.enqueue_batch(
                    document_id=document_id,
                    chunks=chunks,
                    classifications=classifications,
                )
        except Exception:
            logger.warning(
                "ontology classification failed for document_id=%s — continuing without it",
                document_id,
                exc_info=True,
            )
        return chunks

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
    ) -> tuple[list[ParsedChunk], dict[str, Any], dict[str, Any]]:
        """Return (chunks, ocr_metadata, docling_json). All extraction uses Docling."""
        is_pdf = filename.lower().endswith(".pdf")
        chunks, ocr_metadata, doc_json = await self._run_docling(
            filename=filename, content=content
        )
        if is_pdf:
            chunks = await asyncio.to_thread(enhance_table_chunks, content, chunks)
            _body_texts = [
                f"{c.title or ''} {c.text or ''}" for c in chunks[:50]
            ]
            _profile = get_profile_for_document(filename=filename, body_texts=_body_texts)
            chunks = filter_degenerate_table_chunks(chunks, profile=_profile)
            chunks = await _correlate_camelot_tables(content, chunks)
        return chunks, ocr_metadata, doc_json

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
                            orig_table.data = re_table.data
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
        docling_graph: DoclingGraphArtifact | None = None,
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
        if docling_graph is not None:
            (target_dir / "docling_graph.json").write_text(
                docling_graph.model_dump_json(indent=2),
                encoding="utf-8",
            )
