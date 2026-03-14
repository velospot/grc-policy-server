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
        text = " ".join((fields.get("text") or "").split())
        labels = tuple(_item_label_name(item.label) for item in doc_chunk.meta.doc_items)
        captions = _extract_captions(doc_chunk.meta.doc_items, dl_doc)
        source_refs = tuple(item.self_ref for item in doc_chunk.meta.doc_items)
        title = None
        chunk_type: str = "clause"

        if any(item.label == DocItemLabel.TABLE for item in doc_chunk.meta.doc_items):
            chunk_type = "table"
            title = captions[0] if captions else None
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

        metadata: dict[str, Any] = {
            "captions": captions,
            "doc_items_refs": list(source_refs),
        }
        if fields.get("docling_path"):
            metadata["docling_path"] = fields["docling_path"]

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
