from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from grc_policy_server.utils.hashing import normalize_for_comparison

_SECTION_LABEL_RE = re.compile(
    r"^\s*(?:section|clause|article|chapter|annex|appendix)?\s*"
    r"([A-Za-z]?\d+(?:[.\-]\d+)*[A-Za-z]?)\b",
    re.IGNORECASE,
)
_NOTE_LABEL_RE = re.compile(r"\b(note|example|warning|caution)\b", re.IGNORECASE)
_GLOSSARY_TERM_RE = re.compile(
    r"^\s*[\"'“”‘’]?([^:\"'“”‘’\-–—]{2,80})[\"'“”‘’]?\s*[:\-–—]\s*(.+)$",
    re.DOTALL,
)

TEXT_COMPARISON_NODE_TYPES = {
    "clause",
    "paragraph",
    "list_item",
    "note",
    "warning",
    "definition",
    "formula",
}
COMPARISON_NODE_TYPES = TEXT_COMPARISON_NODE_TYPES | {"table"}


@dataclass(frozen=True)
class CanonicalNode:
    node_id: str
    document_id: str
    version_id: str
    parent_id: str | None
    node_type: str
    section_label: str | None
    heading_path: list[str]
    order_index: int
    raw_text: str
    normalized_text: str
    page_from: int | None
    page_to: int | None
    bbox_refs: list[dict[str, Any]]
    language: str = ""
    source_kind: str = "body"
    stable_id: str = ""
    content_hash: str = ""
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_hierarchy_record(
        cls,
        record: dict[str, Any],
        *,
        version_id: str = "1.0",
    ) -> "CanonicalNode":
        metadata = dict(record.get("metadata") or {})
        heading_path = [
            str(part)
            for part in (
                record.get("section_titles")
                or record.get("heading_path")
                or record.get("lineage")
                or []
            )
            if str(part).strip()
        ]
        raw_text = str(record.get("text") or "")
        node_type = _canonical_node_type(record, metadata)
        normalized_text = _glossary_aware_normalized_text(
            str(
                metadata.get("comparison_text")
                or metadata.get("canonical_text")
                or metadata.get("clean_text")
                or record.get("comparison_text")
                or record.get("canonical_text")
                or record.get("clean_text")
                or normalize_for_comparison(raw_text)
            ),
            node_type=node_type,
            heading_path=heading_path,
        )
        page = record.get("page_number")
        if page is None:
            page = record.get("page")
        return cls(
            node_id=str(record.get("node_id") or record.get("chunk_id") or ""),
            document_id=str(record.get("document_id") or ""),
            version_id=version_id,
            parent_id=record.get("parent_id"),
            node_type=node_type,
            section_label=_extract_section_label(record, heading_path),
            heading_path=heading_path,
            order_index=int(record.get("ordinal") or record.get("chunk_index") or 0),
            raw_text=raw_text,
            normalized_text=normalized_text,
            page_from=_coerce_int(page),
            page_to=_coerce_int(record.get("page_to") or page),
            bbox_refs=list(metadata.get("bbox_refs") or record.get("bbox_refs") or []),
            language=str(
                metadata.get("detected_language")
                or record.get("detected_language")
                or ""
            ),
            source_kind=_source_kind(node_type, record, metadata),
            stable_id=str(record.get("stable_id") or ""),
            content_hash=str(record.get("content_hash") or ""),
            title=record.get("title"),
            metadata=metadata,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CanonicalNode":
        return cls(
            node_id=str(payload.get("node_id") or ""),
            document_id=str(payload.get("document_id") or ""),
            version_id=str(payload.get("version_id") or "1.0"),
            parent_id=payload.get("parent_id"),
            node_type=str(payload.get("node_type") or "paragraph"),
            section_label=payload.get("section_label"),
            heading_path=list(payload.get("heading_path") or []),
            order_index=int(payload.get("order_index") or 0),
            raw_text=str(payload.get("raw_text") or ""),
            normalized_text=str(payload.get("normalized_text") or ""),
            page_from=_coerce_int(payload.get("page_from")),
            page_to=_coerce_int(payload.get("page_to")),
            bbox_refs=list(payload.get("bbox_refs") or []),
            language=str(payload.get("language") or ""),
            source_kind=str(payload.get("source_kind") or "body"),
            stable_id=str(payload.get("stable_id") or ""),
            content_hash=str(payload.get("content_hash") or ""),
            title=payload.get("title"),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "parent_id": self.parent_id,
            "node_type": self.node_type,
            "section_label": self.section_label,
            "heading_path": self.heading_path,
            "order_index": self.order_index,
            "raw_text": self.raw_text,
            "normalized_text": self.normalized_text,
            "page_from": self.page_from,
            "page_to": self.page_to,
            "bbox_refs": self.bbox_refs,
            "language": self.language,
            "source_kind": self.source_kind,
            "stable_id": self.stable_id,
            "content_hash": self.content_hash,
            "title": self.title,
            "metadata": self.metadata,
        }

    def to_comparison_record(self) -> dict[str, Any]:
        metadata = dict(self.metadata)
        table_structure = metadata.get("table_structure") or {}
        section_path = " / ".join(self.heading_path) or "Unknown Section"
        return {
            "chunk_id": self.node_id,
            "node_id": self.node_id,
            "canonical_node_id": self.node_id,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "stable_id": self.stable_id,
            "content_hash": self.content_hash,
            "node_type": self.node_type,
            "parent_id": self.parent_id,
            "title": self.title or "",
            "section_label": self.section_label or "",
            "section_path": section_path,
            "heading_path": self.heading_path,
            "text": self.raw_text,
            "clean_text": self.normalized_text,
            "canonical_text": self.normalized_text,
            "comparison_text": str(metadata.get("comparison_text") or self.normalized_text),
            "chunk_index": self.order_index,
            "order_index": self.order_index,
            "page_number": self.page_from,
            "page": self.page_from,
            "page_to": self.page_to,
            "bbox_refs": self.bbox_refs,
            "source_kind": self.source_kind,
            "source": str(metadata.get("source") or "docling"),
            "lineage": self.heading_path,
            "lineage_ids": list(metadata.get("lineage_ids") or []),
            "obligation": str(metadata.get("obligation") or ""),
            "subject": str(metadata.get("subject") or ""),
            "action": str(metadata.get("action") or ""),
            "object": str(metadata.get("object") or ""),
            "condition": str(metadata.get("condition") or ""),
            "markdown_text": str(metadata.get("markdown_text") or metadata.get("table_markdown") or ""),
            "normalized_table_text": str(metadata.get("comparison_text") or "") if self.node_type == "table" else "",
            "comparison_profile": str(metadata.get("comparison_profile") or ""),
            "importance_score": float(metadata.get("importance_score") or 0.0),
            "importance_label": str(metadata.get("importance_label") or ""),
            "low_priority": bool(metadata.get("low_priority", False)),
            "detected_language": self.language,
            "section_summary": str(metadata.get("summary_text") or ""),
            "table_num_rows": int(table_structure.get("num_rows") or 0),
            "table_num_cols": int(table_structure.get("num_cols") or 0),
            "table_cells": list(table_structure.get("cells") or []),
            "table_schema_signature": str(metadata.get("table_schema_signature") or ""),
            "table_row_fingerprints": list(metadata.get("table_row_fingerprints") or []),
            "table_normalized_caption": str(metadata.get("normalized_caption") or ""),
            "canonical_metadata": metadata,
        }


def canonical_nodes_from_hierarchy(
    hierarchy: dict[str, Any],
    *,
    version_id: str = "1.0",
) -> list[CanonicalNode]:
    nodes = hierarchy.get("nodes") if isinstance(hierarchy, dict) else None
    if not isinstance(nodes, list):
        return []
    return [
        CanonicalNode.from_hierarchy_record(node, version_id=version_id)
        for node in nodes
        if isinstance(node, dict)
    ]


def _canonical_node_type(record: dict[str, Any], metadata: dict[str, Any]) -> str:
    raw_type = str(record.get("node_type") or "paragraph").strip().lower()
    if raw_type == "clause":
        labels = " ".join(str(label).lower() for label in metadata.get("source_labels") or [])
        title = str(record.get("title") or "").lower()
        section = str(record.get("section_path") or "").lower()
        if "list" in labels or "bullet" in labels:
            return "list_item"
        if "definition" in labels or "glossary" in section:
            return "definition"
        if _NOTE_LABEL_RE.search(f"{labels} {title}"):
            label = _NOTE_LABEL_RE.search(f"{labels} {title}")
            return "warning" if label and label.group(1).lower() in {"warning", "caution"} else "note"
        return "paragraph"
    return raw_type


def _glossary_aware_normalized_text(
    text: str,
    *,
    node_type: str,
    heading_path: list[str],
) -> str:
    normalized = normalize_for_comparison(text)
    is_glossary = node_type == "definition" or any(
        "glossary" in heading.lower() or "definition" in heading.lower()
        for heading in heading_path
    )
    if not is_glossary:
        return normalized

    match = _GLOSSARY_TERM_RE.match(normalized)
    if not match:
        return normalized
    term = normalize_for_comparison(match.group(1)).strip(" :")
    definition = normalize_for_comparison(match.group(2)).strip()
    if not term or not definition:
        return normalized
    return f"{term}: {definition}"


def _extract_section_label(
    record: dict[str, Any],
    heading_path: list[str],
) -> str | None:
    candidates = [
        str(record.get("title") or ""),
        str(record.get("section_path") or ""),
        *(heading_path[-1:] or []),
    ]
    for candidate in candidates:
        match = _SECTION_LABEL_RE.match(candidate)
        if match:
            return match.group(1).replace("-", ".")
    return None


def _source_kind(
    node_type: str,
    record: dict[str, Any],
    metadata: dict[str, Any],
) -> str:
    if node_type in {"table", "figure", "formula", "note", "warning", "definition"}:
        return node_type
    reason = str(record.get("exclusion_reason") or metadata.get("exclusion_reason") or "")
    if reason:
        return reason
    return "body"


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
