from __future__ import annotations

import io
import logging
import re
from dataclasses import replace
from typing import Any

_TABLE_CAPTION_NUM_RE = re.compile(r"\bTabell?e\s+\d+[A-Za-z]?\b", re.IGNORECASE)
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

    Only acts on table chunks where pdfplumber can produce better results.
    All other chunks are returned unchanged.
    """
    low_quality_indices = [
        i for i, c in enumerate(parsed_chunks)
        if c.chunk_type == "table" and _is_low_quality(c)
    ]
    if not low_quality_indices:
        return parsed_chunks

    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber not installed; skipping table quality enhancement")
        return parsed_chunks

    result = list(parsed_chunks)
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for idx in low_quality_indices:
                chunk = parsed_chunks[idx]
                enhanced = _try_enhance(chunk, pdf)
                if enhanced is not None:
                    result[idx] = enhanced
                    logger.info(
                        "enhanced table page=%s section=%s: replaced column_N headers",
                        chunk.page_number,
                        str(chunk.section_path)[:60],
                    )
    except Exception:
        logger.warning("pdfplumber table enhancement failed; using original extraction", exc_info=True)
    return result


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


def filter_degenerate_table_chunks(chunks: list[ParsedChunk]) -> list[ParsedChunk]:
    """Demote single-column tables whose header reads like a sentence to paragraphs.

    Docling sometimes misclassifies definition lists as 1-column tables where the
    "header" is the full first definition sentence rather than a column label.
    """
    result = []
    for chunk in chunks:
        if chunk.chunk_type == "table" and _is_degenerate_table(chunk):
            result.append(replace(chunk, chunk_type="paragraph"))
        else:
            result.append(chunk)
    return result


def _is_degenerate_table(chunk: ParsedChunk) -> bool:
    ts = chunk.metadata.get("table_structure") or {}
    num_cols = int(ts.get("num_cols") or 0)
    section = str(chunk.section_path or "")

    # A numbered caption ("Tabelle N") anywhere in the section path means Docling
    # confirmed this as a captioned real table — never demote it.
    if _TABLE_CAPTION_NUM_RE.search(section):
        return False

    # Tables in reference sections (Legende, Symbole, Abkürzungen, Definitionen …)
    # without a numbered caption are key-value lists, not normative tables.
    if _REFERENCE_SECTION_RE.search(section):
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
