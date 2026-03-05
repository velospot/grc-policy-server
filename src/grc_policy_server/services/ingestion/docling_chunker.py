from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

from docling_core.transforms.chunker.doc_chunk import DocChunk
from docling_core.transforms.chunker.hierarchical_chunker import (
    HierarchicalChunker,
)
from docling_core.types.doc.labels import DocItemLabel

logger = logging.getLogger(__name__)


def chunk_document(dl_doc, *, merge_list_items: bool) -> list[Any]:
    chunker = HierarchicalChunker(
        merge_list_items=merge_list_items, always_emit_headings=True
    )
    chunksList = list(chunker.chunk(dl_doc))

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
    # doc_items_refs = [it.self_ref for it in doc_chunk.meta.doc_items]
    # has_provInfo = any(it. == DocItemLabel.TABLE for it in doc_items_refs)
    page = extract_page_number(doc_chunk.meta)

    return {
        "text": getattr(chunk, "text", "") or "",
        "docling_path": getattr(chunk, "path", None),
        "page_number": page if isinstance(page, int) else None,
        "section_path": _section_path_from_chunk(doc_chunk),
    }


def meta_to_dict(meta: Any) -> Dict[str, Any]:
    """
    Convert Docling metadata objects (e.g., DocMeta) into a plain dict.

    Supports common patterns:
    - pydantic v2: model_dump()
    - pydantic v1: dict()
    - dataclasses / attrs: __dict__
    - already a dict
    Fallback: best-effort via vars()
    """
    if meta is None:
        return {}

    if isinstance(meta, dict):
        return meta

    # pydantic v2
    if hasattr(meta, "model_dump") and callable(getattr(meta, "model_dump")):
        try:
            return meta.model_dump()
        except Exception:
            pass

    # pydantic v1
    if hasattr(meta, "dict") and callable(getattr(meta, "dict")):
        try:
            return meta.dict()
        except Exception:
            pass

    # generic python object -> __dict__
    if hasattr(meta, "__dict__"):
        try:
            return dict(meta.__dict__)
        except Exception:
            pass

    # last resort
    try:
        return dict(vars(meta))
    except Exception:
        return {}


def iter_dicts(v: Any) -> Iterable[Dict[str, Any]]:
    """
    Yield dicts from: dict | list[dict] | object that can be normalized to dict.
    """
    if v is None:
        return
    if isinstance(v, dict):
        yield v
        return
    if isinstance(v, list):
        for it in v:
            if isinstance(it, dict):
                yield it
            else:
                d = meta_to_dict(it)
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
    """
    Best-effort page extraction. Accepts dict OR DocMeta-like object.

    Priority:
      1) direct keys: page_number/page/page_no/pageNo/page_idx
      2) meta.doc_items[*].prov[*].page_no   (doc_items/prov can be list[dict] or list[obj])
      3) meta.prov[*].page_no                (prov can be list[dict] or list[obj])
      else -1
    """
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
