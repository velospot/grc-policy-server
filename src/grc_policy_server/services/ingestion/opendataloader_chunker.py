from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any

from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk
from grc_policy_server.services.ingestion.table_normalization import (
    extract_headers_from_cells,
    normalize_table_cells,
    rows_from_cells,
    schema_signature,
    table_text_projection,
)

logger = logging.getLogger(__name__)

# OPD element types to skip entirely (page-level decorations)
_SKIP_TYPES = {"header", "footer"}
# OPD element types that are grouping containers — recurse into kids, don't emit
_CONTAINER_TYPES = {"text block"}
# OPD element types that represent section headings
_HEADING_TYPES = {"heading"}
# OPD element types that map to "clause" chunks
_CLAUSE_TYPES = {"paragraph"}
# OPD element types that map to "figure" chunks
_FIGURE_TYPES = {"image", "caption", "formula"}


def parse_opendataloader_elements(elements: list[dict]) -> list[ParsedChunk]:
    """Convert a flat OPD element list (``kids`` from the JSON output) to ParsedChunks.

    Preserves visual reading order. Heading elements update a level-keyed stack
    so that every subsequent content element carries the correct section_path.
    """
    chunks: list[ParsedChunk] = []
    heading_stack: dict[int, str] = {}
    ordinal = 0

    def _current_section_path() -> tuple[str, ...]:
        return tuple(heading_stack[k] for k in sorted(heading_stack))

    def _process(element: dict) -> None:
        nonlocal ordinal
        el_type = (element.get("type") or "").lower()

        # --- page decorations: skip entirely ---
        if el_type in _SKIP_TYPES:
            return

        # --- grouping containers: recurse into kids, don't emit ---
        if el_type in _CONTAINER_TYPES:
            for child in element.get("kids") or []:
                _process(child)
            return

        page = element.get("page number") or None
        bbox = _element_bbox(element)

        # --- headings: update stack ---
        if el_type in _HEADING_TYPES:
            content = (element.get("content") or "").strip()
            if not content:
                return
            level = int(element.get("heading level") or 1)
            heading_stack[level] = content
            # Clear deeper heading levels whenever a shallower one appears
            for k in [k for k in heading_stack if k > level]:
                del heading_stack[k]

            meta: dict[str, Any] = {}
            if bbox:
                meta["page_bbox"] = bbox
            chunks.append(
                ParsedChunk(
                    chunk_type="heading",
                    text=content,
                    section_path=_current_section_path(),
                    page_number=page,
                    ordinal=ordinal,
                    title=content,
                    source="opendataloader",
                    metadata=meta,
                )
            )
            ordinal += 1
            return

        section_path = _current_section_path()

        # --- tables ---
        if el_type == "table":
            chunk = _parse_table(element, section_path, page, ordinal)
            if chunk is not None:
                if bbox:
                    chunk = replace(chunk, metadata={**(chunk.metadata or {}), "page_bbox": bbox})
                chunks.append(chunk)
                ordinal += 1
            return

        # --- lists ---
        if el_type == "list":
            chunk = _parse_list(element, section_path, page, ordinal)
            if chunk is not None:
                if bbox:
                    chunk = replace(chunk, metadata={**(chunk.metadata or {}), "page_bbox": bbox})
                chunks.append(chunk)
                ordinal += 1
            return

        # --- paragraphs / clauses ---
        if el_type in _CLAUSE_TYPES:
            content = (element.get("content") or "").strip()
            if not content:
                return

            # Promote bold short paragraphs that OPD misclassified as paragraph
            if _element_is_bold(element) and len(content) < 200:
                level = int(
                    element.get("heading level")
                    or (max(heading_stack.keys()) + 1 if heading_stack else 1)
                )
                heading_stack[level] = content
                for k in [k for k in heading_stack if k > level]:
                    del heading_stack[k]
                promoted_meta: dict[str, Any] = {"promoted_heading": True}
                if bbox:
                    promoted_meta["page_bbox"] = bbox
                chunks.append(
                    ParsedChunk(
                        chunk_type="heading",
                        text=content,
                        section_path=_current_section_path(),
                        page_number=page,
                        ordinal=ordinal,
                        title=content,
                        source="opendataloader",
                        metadata=promoted_meta,
                    )
                )
                ordinal += 1
                return

            clause_meta: dict[str, Any] = {}
            if bbox:
                clause_meta["page_bbox"] = bbox
            chunks.append(
                ParsedChunk(
                    chunk_type="clause",
                    text=content,
                    section_path=section_path,
                    page_number=page,
                    ordinal=ordinal,
                    source="opendataloader",
                    metadata=clause_meta,
                )
            )
            ordinal += 1
            return

        # --- figures (image, caption, formula) ---
        if el_type in _FIGURE_TYPES:
            content = (element.get("content") or "").strip()
            caption_text = content or element.get("source") or ""
            if not caption_text:
                return
            fig_meta: dict[str, Any] = {"captions": [caption_text]}
            if bbox:
                fig_meta["page_bbox"] = bbox
            chunks.append(
                ParsedChunk(
                    chunk_type="figure",
                    text=caption_text,
                    section_path=section_path,
                    page_number=page,
                    ordinal=ordinal,
                    title=caption_text or None,
                    metadata=fig_meta,
                    source="opendataloader",
                )
            )
            ordinal += 1
            return

    for element in elements:
        _process(element)

    return chunks


# ---------------------------------------------------------------------------
# Table parsing
# ---------------------------------------------------------------------------

def _cell_text(cell: dict) -> str:
    """Extract plain text from an OPD table cell (joins its paragraph kids)."""
    kids = cell.get("kids") or []
    parts = [(k.get("content") or "").strip() for k in kids if k.get("content")]
    if parts:
        return " ".join(parts)
    # Fallback: some cells may have a top-level content field
    return (cell.get("content") or "").strip()


def _cell_is_header(cell: dict) -> bool:
    """Heuristic: if all text in a cell uses a bold font, treat as header."""
    kids = cell.get("kids") or []
    if not kids:
        return False
    return all("bold" in (k.get("font") or "").lower() for k in kids)


def _element_is_bold(element: dict) -> bool:
    """Return True if all text kids of an element use a bold font."""
    kids = element.get("kids") or []
    text_kids = [k for k in kids if k.get("content")]
    if not text_kids:
        return False
    return all("bold" in (k.get("font") or "").lower() for k in text_kids)


def _element_bbox(element: dict) -> dict | None:
    raw = element.get("bounding box") or element.get("bbox") or {}
    if not raw:
        return None
    return {
        "l": raw.get("l"),
        "t": raw.get("t"),
        "r": raw.get("r"),
        "b": raw.get("b"),
    }


def _parse_table(element: dict, section_path: tuple, page: int | None, ordinal: int) -> ParsedChunk | None:
    rows_data = element.get("rows") or []
    num_rows = int(element.get("number of rows") or len(rows_data))
    num_cols = int(element.get("number of columns") or 0)

    if not rows_data:
        return None

    cells: list[dict[str, Any]] = []
    for row in rows_data:
        for cell in row.get("cells") or []:
            row_idx = int(cell.get("row number") or 1) - 1  # 0-indexed
            col_idx = int(cell.get("column number") or 1) - 1
            text = _cell_text(cell)
            is_hdr = (row_idx == 0) or _cell_is_header(cell)
            cells.append(
                {
                    "row": row_idx,
                    "col": col_idx,
                    "row_span": int(cell.get("row span") or 1),
                    "col_span": int(cell.get("column span") or 1),
                    "text": text,
                    "is_header": is_hdr,
                }
            )

    if num_cols == 0 and cells:
        num_cols = max(c["col"] for c in cells) + 1

    normalized = normalize_table_cells(cells)
    headers, header_depth = extract_headers_from_cells(normalized, num_cols)
    rows = rows_from_cells(normalized, headers, header_depth=header_depth)
    sig = schema_signature(headers)
    row_fps = [r["row_fingerprint"] for r in rows]

    # Build markdown table for LLM prompts
    md_lines: list[str] = []
    if headers:
        md_lines.append("| " + " | ".join(headers) + " |")
        md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        vals = [str(row["row_data"].get(h, "")) for h in headers]
        md_lines.append("| " + " | ".join(vals) + " |")
    markdown = "\n".join(md_lines)

    clean_text = table_text_projection(
        table_title="",
        headers=headers,
        rows=[r["row_data"] for r in rows],
    )
    plain_text = markdown or clean_text or _table_plain_text(rows_data)

    if not plain_text and not normalized:
        return None

    return ParsedChunk(
        chunk_type="table",
        text=markdown or plain_text,
        section_path=section_path,
        page_number=page,
        ordinal=ordinal,
        markdown_text=markdown or None,
        source="opendataloader",
        metadata={
            "table_structure": {
                "num_rows": num_rows,
                "num_cols": num_cols,
                "cells": normalized,
            },
            "table_headers": headers,
            "table_header_depth": header_depth,
            "table_schema_signature": sig,
            "table_row_fingerprints": row_fps,
            "table_clean_text": clean_text,
            "table_markdown": markdown,
            "captions": [],
        },
    )


def _table_plain_text(rows_data: list[dict]) -> str:
    """Minimal fallback: join all cell texts row by row."""
    parts: list[str] = []
    for row in rows_data:
        row_texts = [_cell_text(c) for c in (row.get("cells") or [])]
        line = " | ".join(t for t in row_texts if t)
        if line:
            parts.append(line)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# List parsing
# ---------------------------------------------------------------------------

def _parse_list(element: dict, section_path: tuple, page: int | None, ordinal: int) -> ParsedChunk | None:
    items = element.get("list items") or []
    lines: list[str] = []
    for item in items:
        content = (item.get("content") or "").strip()
        # Recurse into nested kids for multi-line list items
        for kid in item.get("kids") or []:
            sub = (kid.get("content") or "").strip()
            if sub:
                content = f"{content} {sub}".strip()
        if content:
            lines.append(f"- {content}")

    if not lines:
        return None

    text = "\n".join(lines)
    return ParsedChunk(
        chunk_type="clause",
        text=text,
        section_path=section_path,
        page_number=page,
        ordinal=ordinal,
        source="opendataloader",
        metadata={"is_list": True},
    )
