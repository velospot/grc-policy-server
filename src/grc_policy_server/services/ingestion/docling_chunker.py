from __future__ import annotations

import logging
from typing import Any

from docling_core.transforms.chunker.doc_chunk import DocChunk
from docling_core.transforms.chunker.hierarchical_chunker import (
    HierarchicalChunker,
)
from docling_core.types.doc.labels import DocItemLabel

logger = logging.getLogger(__name__)


def chunk_document(dl_doc, *, merge_list_items: bool) -> list[Any]:
    chunker = HierarchicalChunker(merge_list_items=merge_list_items)

    chunksList = list(chunker.chunk(dl_doc))
    logger.info(" filechunks ", chunksList[:25])
    return chunksList


def _section_path_from_chunk(doc_chunk: DocChunk) -> list[str]:
    # Docling chunk metadata can include headings/captions depending on version/config.

    headings = []
    meta = getattr(doc_chunk, "meta", None)
    if meta is None:
        return headings

    # Common patterns across versions:
    for attr in ("headings", "heading", "section_headers"):
        val = getattr(meta, attr, None)
        if not val:
            continue
        if isinstance(val, str):
            headings.append(val)
        elif isinstance(val, list):
            headings.extend([str(x) for x in val if x])
    # De-dup while preserving order
    out = []
    for h in headings:
        if h not in out:
            out.append(h)
    return out


def extract_table_and_image_info(
    chunk: Any,
) -> tuple[dict | None, dict | None, list[str]]:
    """
    Returns: (tableInfo_dict, imageInfo_dict, doc_items_refs)
    """
    doc_chunk = DocChunk.model_validate(
        chunk
    )  # shown in Docling examples :contentReference[oaicite:8]{index=8}
    doc_items_refs = [it.self_ref for it in doc_chunk.meta.doc_items]

    has_table = any(it.label == DocItemLabel.TABLE for it in doc_chunk.meta.doc_items)
    has_picture = any(
        it.label == DocItemLabel.PICTURE for it in doc_chunk.meta.doc_items
    )

    table_info = None
    image_info = None

    # The chunk text may already include table serialization depending on serializers.
    # We store it as "markdown" when a table is present.
    if has_table:
        table_info = {
            "caption": getattr(doc_chunk.meta, "caption", None),
            "markdown": getattr(chunk, "text", None),
            "doc_items_refs": doc_items_refs,
        }

    if has_picture:
        # Some pipelines provide picture description/caption in contextualized chunk text.
        image_info = {
            "caption": getattr(doc_chunk.meta, "caption", None),
            "description": None,
            "doc_items_refs": doc_items_refs,
        }

    return table_info, image_info, doc_items_refs


def extract_basic_chunk_fields(chunk: Any) -> dict[str, Any]:
    doc_chunk = DocChunk.model_validate(chunk)
    page = getattr(chunk, "page", None)

    return {
        "text": getattr(chunk, "text", "") or "",
        "docling_path": getattr(chunk, "path", None),
        "page_number": page if isinstance(page, int) else None,
        "section_path": _section_path_from_chunk(doc_chunk),
    }
