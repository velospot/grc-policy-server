from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

NodeType = Literal["document", "section", "clause", "table", "figure"]
ChunkType = Literal["heading", "clause", "table", "figure", "footnote"]


@dataclass(frozen=True)
class ParsedChunk:
    chunk_type: ChunkType
    text: str
    section_path: tuple[str, ...]
    page_number: int | None
    ordinal: int
    title: str | None = None
    markdown_text: str | None = None  # Markdown-formatted text for LLM prompts
    docling_path: str | None = None
    source_refs: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "docling"


@dataclass
class HierarchyNode:
    node_id: str
    stable_id: str
    content_hash: str
    document_id: str
    document_stable_id: str
    node_type: NodeType
    parent_id: str | None
    title: str | None
    text: str
    section_path: str
    section_titles: list[str]
    page_number: int | None
    ordinal: int
    indexable: bool
    excluded_from_index: bool
    exclusion_reason: str | None
    source: str
    lineage: list[str]
    lineage_ids: list[str]
    pure_text_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_graph_record(cls, record: dict[str, Any]) -> "HierarchyNode":
        return cls(
            node_id=str(record.get("node_id") or ""),
            stable_id=str(record.get("stable_id") or ""),
            content_hash=str(record.get("content_hash") or ""),
            document_id=str(record.get("document_id") or ""),
            document_stable_id=str(record.get("document_stable_id") or ""),
            node_type=str(record.get("node_type") or "clause"),  # type: ignore[arg-type]
            parent_id=record.get("parent_id"),
            title=record.get("title"),
            text=str(record.get("text") or ""),
            section_path=str(record.get("section_path") or "Unknown Section"),
            section_titles=list(record.get("section_titles") or []),
            page_number=record.get("page_number"),
            ordinal=int(record.get("ordinal") or 0),
            indexable=bool(record.get("indexable", False)),
            excluded_from_index=bool(record.get("excluded_from_index", False)),
            exclusion_reason=record.get("exclusion_reason"),
            source=str(record.get("source") or "docling"),
            lineage=list(record.get("lineage") or []),
            lineage_ids=list(record.get("lineage_ids") or []),
            metadata=dict(record.get("metadata") or {}),
        )

    @property
    def chunk_id(self) -> str:
        return self.node_id

    def to_vector_record(self) -> dict[str, Any]:
        clean_text = str(self.metadata.get("clean_text") or "")
        # For tables, include structure data for comparison
        table_structure = self.metadata.get("table_structure")
        return {
            "chunk_id": self.node_id,
            "document_id": self.document_id,
            "document_stable_id": self.document_stable_id,
            "stable_id": self.stable_id,
            "content_hash": self.content_hash,
            "node_type": self.node_type,
            "parent_id": self.parent_id,
            "title": self.title or "",
            "section_path": self.section_path or "Unknown Section",
            "text": self.text,
            "clean_text": clean_text,
            "comparison_text": str(self.metadata.get("comparison_text") or ""),
            "chunk_index": self.ordinal,
            "page_number": self.page_number,
            "indexable": self.indexable,
            "excluded_from_index": self.excluded_from_index,
            "exclusion_reason": self.exclusion_reason or "",
            "lineage": self.lineage,
            "lineage_ids": self.lineage_ids,
            "source": self.source,
            "obligation": str(self.metadata.get("obligation") or ""),
            "subject": str(self.metadata.get("subject") or ""),
            "action": str(self.metadata.get("action") or ""),
            "object": str(self.metadata.get("object") or ""),
            "condition": str(self.metadata.get("condition") or ""),
            "markdown_text": str(self.metadata.get("markdown_text") or ""),
            "canonical_text": str(self.metadata.get("comparison_text") or ""),
            "comparison_profile": str(
                self.metadata.get("comparison_profile") or ""
            ),
            "importance_score": float(self.metadata.get("importance_score") or 0.0),
            "importance_label": str(self.metadata.get("importance_label") or ""),
            "low_priority": bool(self.metadata.get("low_priority", False)),
            "detected_language": str(self.metadata.get("detected_language") or ""),
            "section_summary": str(self.metadata.get("summary_text") or ""),
            # Table structure for structural comparison
            "table_num_rows": table_structure.get("num_rows", 0) if table_structure else 0,
            "table_num_cols": table_structure.get("num_cols", 0) if table_structure else 0,
            "table_cells": table_structure.get("cells", []) if table_structure else [],
            "table_schema_signature": str(
                self.metadata.get("table_schema_signature") or ""
            ),
            "table_row_fingerprints": list(
                self.metadata.get("table_row_fingerprints") or []
            ),
            "table_normalized_caption": str(
                self.metadata.get("normalized_caption") or ""
            ),
        }

    def to_graph_record(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "stable_id": self.stable_id,
            "content_hash": self.content_hash,
            "document_id": self.document_id,
            "document_stable_id": self.document_stable_id,
            "node_type": self.node_type,
            "parent_id": self.parent_id,
            "title": self.title,
            "text": self.text,
            "section_path": self.section_path or "Unknown Section",
            "section_titles": self.section_titles,
            "page_number": self.page_number,
            "ordinal": self.ordinal,
            "indexable": self.indexable,
            "excluded_from_index": self.excluded_from_index,
            "exclusion_reason": self.exclusion_reason,
            "source": self.source,
            "lineage": self.lineage,
            "lineage_ids": self.lineage_ids,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class DocumentHierarchy:
    document_id: str
    document_stable_id: str
    document_family: str
    content_hash: str
    nodes: list[HierarchyNode]
    indexable_nodes: list[HierarchyNode]
    metadata: dict[str, Any]
