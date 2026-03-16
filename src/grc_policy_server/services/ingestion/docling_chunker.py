from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, Optional

from docling_core.transforms.chunker.doc_chunk import DocChunk
from docling_core.transforms.chunker.hierarchical_chunker import (
    HierarchicalChunker,
)
from docling_core.types.doc.labels import DocItemLabel

from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk

logger = logging.getLogger(__name__)


def _extract_table_structure(doc_chunk: DocChunk, dl_doc: Any) -> dict[str, Any] | None:
    """Extract row/column structure from docling table data."""
    for item in doc_chunk.meta.doc_items:
        if item.label != DocItemLabel.TABLE:
            continue

        ref = item.self_ref  # e.g., "#/tables/0"
        logger.debug("Found TABLE item with ref=%s", ref)
        if not ref:
            logger.debug("No self_ref for table item")
            continue

        # Resolve reference to get table data
        table_data = _resolve_table_ref(dl_doc, ref)
        if not table_data:
            logger.debug("Could not resolve table ref=%s, dl_doc type=%s", ref, type(dl_doc))
            continue

        data = table_data.get("data", {})
        if not data:
            logger.debug("No 'data' key in table_data for ref=%s", ref)
            continue

        cells = []
        for cell in data.get("table_cells", []):
            cells.append({
                "row": cell.get("start_row_offset_idx", 0),
                "col": cell.get("start_col_offset_idx", 0),
                "row_span": cell.get("row_span", 1),
                "col_span": cell.get("col_span", 1),
                "text": cell.get("text", ""),
                "is_header": cell.get("column_header", False) or cell.get("row_header", False),
            })

        logger.info("Extracted table structure: %d rows x %d cols, %d cells",
                   data.get("num_rows", 0), data.get("num_cols", 0), len(cells))

        return {
            "num_rows": data.get("num_rows", 0),
            "num_cols": data.get("num_cols", 0),
            "cells": cells,
            "grid": data.get("grid"),
        }

    return None


def _resolve_table_ref(dl_doc: Any, ref: str) -> dict[str, Any] | None:
    """Resolve a docling reference like '#/tables/0' to actual table data."""
    if not ref or not ref.startswith("#/"):
        return None

    parts = ref[2:].split("/")  # Remove "#/" and split
    if len(parts) < 2:
        return None

    collection_name = parts[0]  # e.g., "tables"
    key = parts[1]  # e.g., "0" or some identifier

    # Try to get doc as dict
    doc_dict = None
    if hasattr(dl_doc, "export_to_dict") and callable(getattr(dl_doc, "export_to_dict")):
        try:
            doc_dict = dl_doc.export_to_dict()
        except Exception as e:
            logger.debug("export_to_dict failed: %s", e)

    if doc_dict is None and hasattr(dl_doc, "model_dump") and callable(getattr(dl_doc, "model_dump")):
        try:
            doc_dict = dl_doc.model_dump()
        except Exception as e:
            logger.debug("model_dump failed: %s", e)

    if doc_dict is None and isinstance(dl_doc, dict):
        doc_dict = dl_doc

    if doc_dict is None:
        logger.debug("Could not convert dl_doc to dict, type=%s", type(dl_doc))
        return None

    collection = doc_dict.get(collection_name, [])

    # Handle list (most common for tables)
    if isinstance(collection, list):
        try:
            idx = int(key)
            if 0 <= idx < len(collection):
                return collection[idx]
        except (ValueError, IndexError):
            pass
        # Also try matching by self_ref
        for item in collection:
            if isinstance(item, dict) and item.get("self_ref") == ref:
                return item

    # Handle dict
    elif isinstance(collection, dict):
        return collection.get(key) or collection.get(ref)

    return None


def _table_structure_to_markdown(struct: dict[str, Any], title: str | None = None) -> str:
    """Convert table structure to proper markdown table format."""
    if not struct or not struct.get("cells"):
        return ""

    num_rows = struct.get("num_rows", 0)
    num_cols = struct.get("num_cols", 0)

    if num_rows == 0 or num_cols == 0:
        return ""

    # Build grid from cells
    grid = [["" for _ in range(num_cols)] for _ in range(num_rows)]

    for cell in struct.get("cells", []):
        row = cell.get("row", 0)
        col = cell.get("col", 0)
        text = str(cell.get("text", "")).strip()
        # Replace pipe characters to avoid breaking markdown
        text = text.replace("|", "\\|")
        if 0 <= row < num_rows and 0 <= col < num_cols:
            grid[row][col] = text

    # Generate markdown
    lines = []
    if title:
        lines.append(f"**{title}**")
        lines.append("")

    for i, row in enumerate(grid):
        line = "| " + " | ".join(cell if cell else "" for cell in row) + " |"
        lines.append(line)
        if i == 0:  # Add header separator after first row
            lines.append("|" + "|".join(["---"] * num_cols) + "|")

    return "\n".join(lines)


def _table_structure_to_clean_text(struct: dict[str, Any], title: str | None = None) -> str:
    """Convert table structure to clean text for embedding/comparison."""
    if not struct or not struct.get("cells"):
        return ""

    parts = []
    if title:
        parts.append(f"Table: {title}")

    num_rows = struct.get("num_rows", 0)
    num_cols = struct.get("num_cols", 0)
    parts.append(f"Dimensions: {num_rows} rows x {num_cols} columns")

    # Group cells by row for readable output
    rows_data: dict[int, list[str]] = {}
    for cell in struct.get("cells", []):
        row_idx = cell.get("row", 0)
        text = str(cell.get("text", "")).strip()
        if text:
            rows_data.setdefault(row_idx, []).append(text)

    # Build clean text representation
    for row_idx in sorted(rows_data.keys()):
        row_text = " | ".join(rows_data[row_idx])
        parts.append(row_text)

    return "\n".join(parts)

_HEADER_FOOTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^page\\s+\\d+(\\s+of\\s+\\d+)?$", re.IGNORECASE),
    re.compile(r"^\\d+\\s*/\\s*\\d+$"),
    re.compile(r"^\\d{1,2}[/.-]\\d{1,2}[/.-]\\d{2,4}$"),
    re.compile(
        r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\\s+\\d{1,2},?\\s+\\d{2,4}$",
        re.IGNORECASE,
    ),
    re.compile(r"^(document\\s+id|doc\\s*id|record\\s*id)[:#]?\\s*[-A-Za-z0-9_.]+$", re.IGNORECASE),
    re.compile(r"^[A-Z]{2,5}-\\d{2,}$"),
)

def chunk_document(dl_doc, *, merge_list_items: bool) -> list[Any]:
    chunker = HierarchicalChunker(
        merge_list_items=merge_list_items, always_emit_headings=True
    )
    chunks_list = list(chunker.chunk(dl_doc))
    return chunks_list


def parse_docling_chunks(dl_doc, raw_chunks: Iterable[Any]) -> list[ParsedChunk]:
    parsed_chunks: list[ParsedChunk] = []
    for idx, raw_chunk in enumerate(raw_chunks):
        doc_chunk = DocChunk.model_validate(raw_chunk)
        fields = extract_basic_chunk_fields(raw_chunk)
        # DocChunk.text is already markdown-formatted; preserve it before normalizing
        markdown_text = getattr(raw_chunk, "text", None) or None
        text = " ".join((fields.get("text") or "").split())
        labels = tuple(_item_label_name(item.label) for item in doc_chunk.meta.doc_items)
        captions = _extract_captions(doc_chunk.meta.doc_items, dl_doc)
        source_refs = tuple(item.self_ref for item in doc_chunk.meta.doc_items)
        title = None
        chunk_type: str = "clause"

        # Initialize metadata first so we can add to it
        metadata: dict[str, Any] = {
            "captions": captions,
            "doc_items_refs": list(source_refs),
        }
        if fields.get("docling_path"):
            metadata["docling_path"] = fields["docling_path"]

        if any(item.label == DocItemLabel.TABLE for item in doc_chunk.meta.doc_items):
            chunk_type = "table"
            title = captions[0] if captions else None

            # Log doc_items for debugging
            for item in doc_chunk.meta.doc_items:
                logger.info("Table chunk doc_item: label=%s, self_ref=%s", item.label, getattr(item, 'self_ref', None))

            # Extract table structure from docling
            table_struct = _extract_table_structure(doc_chunk, dl_doc)
            logger.info("Table structure extraction result: %s", "SUCCESS" if table_struct else "FAILED")
            if table_struct:
                metadata["table_structure"] = {
                    "num_rows": table_struct.get("num_rows", 0),
                    "num_cols": table_struct.get("num_cols", 0),
                    "cells": table_struct.get("cells", []),
                }
                # Generate proper markdown from structure
                struct_markdown = _table_structure_to_markdown(table_struct, title)
                if struct_markdown:
                    markdown_text = struct_markdown
                # Generate better clean_text for embeddings
                struct_clean_text = _table_structure_to_clean_text(table_struct, title)
                if struct_clean_text:
                    metadata["table_clean_text"] = struct_clean_text

        elif any(item.label == DocItemLabel.PICTURE for item in doc_chunk.meta.doc_items):
            chunk_type = "figure"
            title = captions[0] if captions else None
        elif _is_heading_only(doc_chunk, text):
            chunk_type = "heading"
            heading_path = fields.get("section_path") or []
            title = heading_path[-1] if heading_path else None
        elif not text and captions:
            chunk_type = "figure"
            title = captions[0]
            text = captions[0]

        if _is_header_footer_text(text):
            continue

        parsed_chunks.append(
            ParsedChunk(
                chunk_type=chunk_type,  # type: ignore[arg-type]
                text=text,
                section_path=tuple(fields.get("section_path") or ()),
                page_number=fields.get("page_number"),
                ordinal=idx,
                title=title,
                markdown_text=markdown_text,
                docling_path=fields.get("docling_path"),
                source_refs=source_refs,
                labels=labels,
                metadata=metadata,
                source="docling",
            )
        )

    return parsed_chunks


def _section_path_from_chunk(doc_chunk: DocChunk) -> list[str]:
    headings = []
    meta = getattr(doc_chunk, "meta", None)
    if meta is None:
        return headings

    for attr in ("headings", "heading", "section_headers"):
        val = getattr(meta, attr, None)
        if not val:
            continue
        if isinstance(val, str):
            headings.append(val)
        elif isinstance(val, list):
            headings.extend([str(x) for x in val if x])

    out = []
    for heading in headings:
        if heading not in out:
            out.append(heading)
    return out


def extract_table_and_image_info(
    chunk: Any,
) -> tuple[dict | None, dict | None, list[str]]:
    doc_chunk = DocChunk.model_validate(chunk)
    doc_items_refs = [it.self_ref for it in doc_chunk.meta.doc_items]

    has_table = any(it.label == DocItemLabel.TABLE for it in doc_chunk.meta.doc_items)
    has_picture = any(
        it.label == DocItemLabel.PICTURE for it in doc_chunk.meta.doc_items
    )

    table_info = None
    image_info = None

    if has_table:
        table_info = {
            "caption": getattr(doc_chunk.meta, "caption", None),
            "markdown": getattr(chunk, "text", None),
            "doc_items_refs": doc_items_refs,
        }

    if has_picture:
        image_info = {
            "caption": getattr(doc_chunk.meta, "caption", None),
            "description": None,
            "doc_items_refs": doc_items_refs,
        }

    return table_info, image_info, doc_items_refs


def extract_basic_chunk_fields(chunk: Any) -> dict[str, Any]:
    doc_chunk = DocChunk.model_validate(chunk)
    page = extract_page_number(doc_chunk.meta)

    return {
        "text": getattr(chunk, "text", "") or "",
        "docling_path": getattr(chunk, "path", None),
        "page_number": page if isinstance(page, int) and page >= 0 else None,
        "section_path": _section_path_from_chunk(doc_chunk),
    }


def meta_to_dict(meta: Any) -> Dict[str, Any]:
    if meta is None:
        return {}

    if isinstance(meta, dict):
        return meta

    if hasattr(meta, "model_dump") and callable(getattr(meta, "model_dump")):
        try:
            return meta.model_dump()
        except Exception:
            pass

    if hasattr(meta, "dict") and callable(getattr(meta, "dict")):
        try:
            return meta.dict()
        except Exception:
            pass

    if hasattr(meta, "__dict__"):
        try:
            return dict(meta.__dict__)
        except Exception:
            pass

    try:
        return dict(vars(meta))
    except Exception:
        return {}


def iter_dicts(v: Any) -> Iterable[Dict[str, Any]]:
    if v is None:
        return
    if isinstance(v, dict):
        yield v
        return
    if isinstance(v, list):
        for item in v:
            if isinstance(item, dict):
                yield item
            else:
                d = meta_to_dict(item)
                if d:
                    yield d
        return

    d = meta_to_dict(v)
    if d:
        yield d


def safe_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


def extract_page_number(meta_any: Any) -> int:
    meta = meta_to_dict(meta_any)

    for key in ("page_number", "page", "page_no", "pageNo", "page_idx"):
        n = safe_int(meta.get(key))
        if n is not None:
            return n

    for doc_item in iter_dicts(meta.get("doc_items")):
        for prov in iter_dicts(doc_item.get("prov")):
            n = safe_int(prov.get("page_no"))
            if n is not None:
                return n

    for prov in iter_dicts(meta.get("prov")):
        n = safe_int(prov.get("page_no"))
        if n is not None:
            return n

    return -1


def _extract_captions(doc_items: Iterable[Any], dl_doc: Any) -> list[str]:
    captions: list[str] = []
    for item in doc_items:
        if hasattr(item, "caption_text") and callable(getattr(item, "caption_text")):
            try:
                caption = item.caption_text(dl_doc).strip()
            except Exception:
                caption = ""
            if caption and caption not in captions:
                captions.append(caption)
    return captions


def _is_header_footer_text(text: str) -> bool:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return False

    for pattern in _HEADER_FOOTER_PATTERNS:
        if pattern.match(normalized):
            return True

    digits = sum(ch.isdigit() for ch in normalized)
    if len(normalized) <= 12 and digits >= 0.6 * len(normalized):
        return True

    return False


def _is_heading_only(doc_chunk: DocChunk, text: str) -> bool:
    if text:
        return False
    return any(
        item.label in {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER}
        for item in doc_chunk.meta.doc_items
    )


def _item_label_name(label: Any) -> str:
    value = getattr(label, "value", label)
    return str(value)
