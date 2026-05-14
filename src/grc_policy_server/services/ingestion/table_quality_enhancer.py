from __future__ import annotations

import io
import logging
import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grc_policy_server.services.ingestion.document_family_profile import DocumentFamilyProfile

# Matches numbered table captions in German (Tabelle 3, Tabelle A.1) and English (Table 2, Table 23)
_TABLE_CAPTION_NUM_RE = re.compile(
    r"\b(?:Tabell?e|Table)\s+(?:[A-Z]\.)?\d+(?:\.\d+)*[A-Za-z]?\b",
    re.IGNORECASE,
)
# Section headings that indicate reference/legend content, not normative tables.
# Tables in these sections without a numbered caption are lists, not tables.
_REFERENCE_SECTION_RE = re.compile(
    r"\b(legende|symbole?|abkürzung|definitionen?|begriffe?|inhalt|glossar"
    r"|annex|anhang|abbreviation|legend|symbol|glossary|definition)\b",
    re.IGNORECASE,
)

from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk
from grc_policy_server.services.ingestion.table_normalization import (
    extract_headers_from_cells,
    normalize_table_cells,
    rows_from_cells,
    schema_signature,
    table_text_projection,
)

logger = logging.getLogger(__name__)


def enhance_table_chunks(
    pdf_bytes: bytes,
    parsed_chunks: list[ParsedChunk],
) -> list[ParsedChunk]:
    """Re-extract tables with column_N placeholder headers using pdfplumber.

    DISABLED: pdfplumber was over-detecting tables, classifying non-table content
    (like legend items on page boundaries) as tables. This caused false positives in
    comparison. The degenerate table filter is more effective at catching false tables.
    See: https://github.com/anthropics/grc-policy-server/issues/XXX
    """
    return parsed_chunks


def _is_low_quality(chunk: ParsedChunk) -> bool:
    headers = chunk.metadata.get("table_headers") or []
    sig = str(chunk.metadata.get("table_schema_signature") or "")
    return any(str(h).startswith("column_") for h in headers) or "column_" in sig


def _try_enhance(chunk: ParsedChunk, pdf: Any) -> ParsedChunk | None:
    page_number = chunk.page_number
    if page_number is None or page_number < 1 or page_number > len(pdf.pages):
        return None

    page = pdf.pages[page_number - 1]
    tables = page.extract_tables()
    if not tables:
        return None

    expected_cols = int((chunk.metadata.get("table_structure") or {}).get("num_cols") or 0)
    best = min(
        tables,
        key=lambda t: abs(max((len(r) for r in t if r), default=0) - expected_cols),
    )
    cells = _rows_to_cells(best)
    if not cells:
        return None

    num_cols = max(c["col"] for c in cells) + 1
    normalized = normalize_table_cells(cells)
    headers, header_depth = extract_headers_from_cells(normalized, num_cols)
    column_n_count = sum(1 for h in headers if h.startswith("column_"))
    if column_n_count > len(headers) // 2:
        return None

    rows = rows_from_cells(normalized, headers, header_depth=header_depth)
    sig = schema_signature(headers)
    row_fps = [r["row_fingerprint"] for r in rows]

    md_lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        md_lines.append(
            "| " + " | ".join(str(row["row_data"].get(h, "")) for h in headers) + " |"
        )
    markdown = "\n".join(md_lines)

    clean_text = table_text_projection("", headers, [r["row_data"] for r in rows])

    new_metadata = dict(chunk.metadata)
    new_metadata["table_structure"] = {
        "num_rows": len(rows),
        "num_cols": num_cols,
        "cells": normalized,
    }
    new_metadata["table_headers"] = headers
    new_metadata["table_header_depth"] = header_depth
    new_metadata["table_schema_signature"] = sig
    new_metadata["table_row_fingerprints"] = row_fps
    new_metadata["table_markdown"] = markdown
    new_metadata["table_clean_text"] = clean_text
    new_metadata["table_enhanced_by"] = "pdfplumber"

    return replace(chunk, text=markdown, markdown_text=markdown, metadata=new_metadata)


_MAX_HEADER_LEN_FOR_REAL_TABLE = 80
_MIN_TABLE_ROWS = 2
_MIN_TABLE_COLS = 2
_MIN_CELL_FILL_PERCENT = 40  # Tables must be at least 40% filled with cells


def filter_degenerate_table_chunks(
    chunks: list[ParsedChunk],
    profile: "DocumentFamilyProfile | None" = None,
) -> list[ParsedChunk]:
    """Demote single-column tables whose header reads like a sentence to paragraphs.

    Docling sometimes misclassifies definition lists as 1-column tables where the
    "header" is the full first definition sentence rather than a column label.
    Pass *profile* to enable conservative mode for document families (e.g. DIN, DNV)
    where structural heuristics are too aggressive.
    """
    result = []
    for chunk in chunks:
        if chunk.chunk_type == "table" and _is_degenerate_table(chunk, profile=profile):
            result.append(replace(chunk, chunk_type="paragraph"))
        else:
            result.append(chunk)
    return result


def _validate_table_structure(chunk: ParsedChunk) -> bool:
    """Check if chunk has minimal table structure (not sparse/list-like).

    Returns:
        True if table has valid structure, False if sparse/list-like.
    """
    ts = chunk.metadata.get("table_structure") or {}
    num_rows = int(ts.get("num_rows") or 0)
    num_cols = int(ts.get("num_cols") or 0)

    # Must have minimum dimensions
    if num_rows < _MIN_TABLE_ROWS or num_cols < _MIN_TABLE_COLS:
        return False

    # Check cell density: actual cells / (rows × cols)
    # Cells are stored at metadata["table_structure"]["cells"], not at the top level.
    cells = (chunk.metadata.get("table_structure") or {}).get("cells") or []
    expected_cells = num_rows * num_cols
    cell_fill_percent = (len(cells) / expected_cells * 100) if expected_cells > 0 else 0

    if cell_fill_percent < _MIN_CELL_FILL_PERCENT:
        return False  # Sparse table, likely a list

    return True


def _is_list_like_table(chunk: ParsedChunk) -> bool:
    """Detect if table structure is actually a numbered/bulleted list.

    Returns:
        True if content appears list-like, False if it appears table-like.
    """
    cells = (chunk.metadata.get("table_structure") or {}).get("cells") or []
    if not cells:
        return False

    # Pattern: First column contains numbers/bullets (5.4.4.2.1, •, -, →, etc.)
    list_marker_pattern = re.compile(r"^[\d\s\.\-•\*\→]+$")
    first_col_cells = [c for c in cells if c.get("col") == 0]

    # If most cells in first column match list patterns, it's list-like
    if first_col_cells:
        matching = sum(
            1
            for c in first_col_cells
            if list_marker_pattern.match(str(c.get("text", "")))
        )
        if len(first_col_cells) > 0 and matching / len(first_col_cells) >= 0.7:
            return True

    # Pattern: Table has only 1-2 real columns, rest are empty
    non_empty_cols = {c.get("col") for c in cells if c.get("text", "").strip()}
    if len(non_empty_cols) <= 2:
        return True

    return False


def _is_degenerate_table(
    chunk: ParsedChunk,
    profile: "DocumentFamilyProfile | None" = None,
) -> bool:
    ts = chunk.metadata.get("table_structure") or {}
    num_cols = int(ts.get("num_cols") or 0)
    section = str(chunk.section_path or "")

    # A numbered caption ("Tabelle N" / "Table N") anywhere in the section path means
    # Docling confirmed this as a captioned real table — never demote it.
    if _TABLE_CAPTION_NUM_RE.search(section):
        return False

    # Tables in reference sections (Legende, Symbole, Abkürzungen, Definitionen …)
    # without a numbered caption are key-value lists, not normative tables.
    if _REFERENCE_SECTION_RE.search(section):
        return True

    # Conservative mode: technical standard families (DIN, DNV) very rarely produce
    # genuinely degenerate tables. Skip structural/list heuristics — only the
    # sentence-header check below applies.
    conservative = profile is not None and getattr(profile, "conservative_table_filter", False)

    if not conservative:
        # Structural validation - reject sparse/minimal tables
        if not _validate_table_structure(chunk):
            return True

        # Content heuristics - detect list-like patterns
        if _is_list_like_table(chunk):
            return True

    # Single-column table whose header reads like a sentence (not a column label)
    # is almost certainly a definition list that Docling misclassified as a table.
    if num_cols == 1:
        headers = chunk.metadata.get("table_headers") or []
        if headers and len(str(headers[0])) > _MAX_HEADER_LEN_FOR_REAL_TABLE:
            return True

    return False


def _rows_to_cells(rows: list[list[str | None]]) -> list[dict]:
    """Convert pdfplumber row-list output to canonical cell dicts."""
    if not rows:
        return []
    cells = []
    for row_idx, row in enumerate(rows):
        for col_idx, text in enumerate(row or []):
            cells.append({
                "row": row_idx,
                "col": col_idx,
                "row_span": 1,
                "col_span": 1,
                "text": (text or "").strip(),
                "is_header": row_idx == 0,
            })
    return cells


async def hybrid_extract_tables_async(
    pdf_bytes: bytes,
    parsed_chunks: list[ParsedChunk],
) -> list[ParsedChunk]:
    """Enhance table extraction using ensemble approach with Docling as primary.

    Strategy:
    1. Keep Docling extraction for section hierarchy preservation
    2. Use TableExtractorEnsemble as fallback for accuracy improvement
    3. Replace Docling table chunks with ensemble results if quality improves
    4. Return enhanced chunks with best extraction from either source

    Args:
        pdf_bytes: PDF content as bytes
        parsed_chunks: Chunks from Docling extraction

    Returns:
        List of enhanced ParsedChunk objects with improved table extractions
    """
    try:
        import tempfile
        from pathlib import Path

        from grc_policy_server.services.ingestion.table_extraction_ensemble import (
            TableExtractorEnsemble,
        )

        # Write PDF to temp file (required by ensemble API)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            # Extract with ensemble
            ensemble = TableExtractorEnsemble()
            candidates = await ensemble.extract_tables(tmp_path)

            if not candidates:
                logger.debug("Ensemble extraction found no tables, using Docling only")
                return parsed_chunks

            logger.info(f"Ensemble extracted {len(candidates)} candidate tables")

            # Build page-to-section-path mapping from Docling chunks
            page_to_section: dict[int, str] = {}
            docling_tables: dict[int, ParsedChunk] = {}  # page -> Docling table chunk

            for chunk in parsed_chunks:
                if chunk.chunk_type == "table" and chunk.page_number is not None:
                    section = str(chunk.section_path or "")
                    page_to_section[chunk.page_number] = section
                    docling_tables[chunk.page_number] = chunk

            # Match and enhance Docling tables with ensemble results
            enhanced = list(parsed_chunks)
            matched_pages = set()

            for candidate in candidates:
                page = candidate.page_number
                if page not in docling_tables:
                    logger.debug(
                        f"Ensemble found table on page {page} not in Docling, skipping"
                    )
                    continue

                matched_pages.add(page)
                docling_chunk = docling_tables[page]

                # Check if ensemble result is better quality
                if _is_ensemble_better(docling_chunk, candidate):
                    # Replace Docling chunk with ensemble-enhanced version
                    enhanced_chunk = _apply_ensemble_enhancement(docling_chunk, candidate)
                    # Find and replace in enhanced list
                    for i, chunk in enumerate(enhanced):
                        if chunk is docling_chunk:
                            enhanced[i] = enhanced_chunk
                            logger.info(
                                f"Enhanced table on page {page} with ensemble result"
                            )
                            break

            return enhanced

        finally:
            # Clean up temp file
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"Hybrid extraction failed, using Docling only: {e}")
        return parsed_chunks


def _is_ensemble_better(docling_chunk: ParsedChunk, candidate: Any) -> bool:
    """Check if ensemble candidate is better quality than Docling extraction.

    Quality criteria:
    - Confidence > 0.7
    - Most headers are real (not column_N)
    - Valid table structure (>= 2 rows and cols)

    Args:
        docling_chunk: Original Docling table chunk
        candidate: Ensemble TableCandidate

    Returns:
        True if ensemble result should replace Docling
    """
    # Low confidence threshold
    if candidate.confidence < 0.7:
        return False

    # Check header quality
    real_headers = sum(1 for h in candidate.headers if not str(h).startswith("column_"))
    if not candidate.headers or real_headers / len(candidate.headers) < 0.5:
        return False

    # Ensure minimum structure
    if candidate.num_rows < 2 or candidate.num_cols < 2:
        return False

    # Don't replace already good Docling extractions
    docling_headers = docling_chunk.metadata.get("table_headers") or []
    if docling_headers:
        docling_real = sum(
            1 for h in docling_headers if not str(h).startswith("column_")
        )
        if docling_real == len(docling_headers):
            # Docling has perfect headers, keep it
            return False

    return True


def _apply_ensemble_enhancement(
    docling_chunk: ParsedChunk,
    candidate: Any,
) -> ParsedChunk:
    """Apply ensemble extraction as enhancement to Docling chunk.

    Updates table metadata with ensemble results while preserving section context.

    Args:
        docling_chunk: Original Docling ParsedChunk
        candidate: Ensemble TableCandidate with improved extraction

    Returns:
        Enhanced ParsedChunk with ensemble table data
    """
    # Convert candidate cells to normalized format
    cells = [
        {
            "row": c.get("row", 0),
            "col": c.get("col", 0),
            "text": c.get("text", "").strip(),
            "rowspan": c.get("rowspan", 1),
            "colspan": c.get("colspan", 1),
            "is_header": c.get("is_header", False),
        }
        for c in candidate.cells
    ]

    normalized = normalize_table_cells(cells)
    headers, header_depth = extract_headers_from_cells(normalized, candidate.num_cols)

    # Build rows
    rows = rows_from_cells(normalized, headers, header_depth=header_depth)
    sig = schema_signature(headers)
    row_fps = [r["row_fingerprint"] for r in rows]

    # Create markdown representation
    md_lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        md_lines.append(
            "| " + " | ".join(str(row["row_data"].get(h, "")) for h in headers) + " |"
        )
    markdown = "\n".join(md_lines)

    clean_text = table_text_projection("", headers, [r["row_data"] for r in rows])

    # Update metadata
    new_metadata = dict(docling_chunk.metadata)
    new_metadata["table_structure"] = {
        "num_rows": len(rows),
        "num_cols": candidate.num_cols,
        "cells": normalized,
    }
    new_metadata["table_headers"] = headers
    new_metadata["table_header_depth"] = header_depth
    new_metadata["table_schema_signature"] = sig
    new_metadata["table_row_fingerprints"] = row_fps
    new_metadata["table_markdown"] = markdown
    new_metadata["table_clean_text"] = clean_text
    new_metadata["table_extraction_source"] = "ensemble"
    new_metadata["extraction_backend"] = candidate.backend_name
    new_metadata["extraction_confidence"] = candidate.confidence

    return replace(
        docling_chunk,
        text=markdown,
        markdown_text=markdown,
        metadata=new_metadata,
    )


def _run_async_hybrid_extract(pdf_bytes: bytes, parsed_chunks: list[ParsedChunk]) -> list[ParsedChunk]:
    """Sync wrapper for async hybrid_extract_tables_async.

    This wrapper is used when called via asyncio.to_thread() from an async context.
    """
    import asyncio
    try:
        # Try to get existing event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Can't use run_until_complete on a running loop
            # Fall back to Docling only
            logger.debug("Hybrid extraction not available in running loop context")
            return parsed_chunks
        return loop.run_until_complete(hybrid_extract_tables_async(pdf_bytes, parsed_chunks))
    except RuntimeError:
        # No event loop, create new one
        return asyncio.run(hybrid_extract_tables_async(pdf_bytes, parsed_chunks))
