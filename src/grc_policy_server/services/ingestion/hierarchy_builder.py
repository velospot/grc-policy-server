from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import re
from typing import Iterable

from grc_policy_server.services.ingestion.hierarchy_models import (
    DocumentHierarchy,
    HierarchyNode,
    ParsedChunk,
)
from grc_policy_server.utils.hashing import normalize_text, sha256_hex, slugify_text, stable_uuid

_TOC_TITLES = {
    "contents",
    "table of contents",
    "toc",
    "index",
    "document index",
}
_TOC_LINE_RE = re.compile(
    r"^\s*(?:[A-Za-z0-9][A-Za-z0-9 .,'()\-/]{2,}?)(?:\.{2,}|\s{2,})(?:[A-Za-z]?\d+[A-Za-z]?)\s*$"
)
_CLAUSE_MARKER_RE = re.compile(
    r"^\s*((?:section|clause|article|appendix|annex)?\s*[A-Za-z]?\d+(?:\.\d+)*[A-Za-z]?)\b",
    re.IGNORECASE,
)
_VERSION_SUFFIX_RE = re.compile(
    r"(?i)(?:[-_ ](?:v(?:ersion)?[-_ ]?)?\d+(?:\.\d+)*)$"
)
_MAX_SECTION_TEXT = 5000


def build_document_hierarchy(
    *,
    document_id: str,
    filename: str,
    parsed_chunks: Iterable[ParsedChunk],
    content_hash: str,
) -> DocumentHierarchy:
    doc_family = document_family_from_filename(filename)
    document_stable_id = stable_uuid(f"document::{doc_family}")

    nodes: list[HierarchyNode] = []
    section_nodes: dict[tuple[str, ...], HierarchyNode] = {}
    section_buffers: dict[tuple[str, ...], list[str]] = defaultdict(list)
    section_exclusions: dict[tuple[str, ...], str] = {}
    section_leaf_ordinals: dict[tuple[str, ...], int] = defaultdict(int)
    section_ordinal = 0

    for chunk in sorted(parsed_chunks, key=lambda item: (item.page_number or 0, item.ordinal)):
        section_titles = _normalize_section_titles(chunk)
        section_path = " / ".join(section_titles) if section_titles else "Unknown Section"
        inherited_reason = _get_inherited_exclusion(section_titles, section_exclusions)
        section_lineage_ids = [document_id]

        if section_titles:
            parent_id = document_id
            lineage: list[str] = []
            for depth, title in enumerate(section_titles, start=1):
                current_path = section_titles[:depth]
                current_reason = section_exclusions.get(current_path)
                if current_reason is None:
                    current_reason = _classify_toc_block(
                        title=title,
                        section_titles=current_path,
                        text="" if depth != len(section_titles) else chunk.text,
                        labels=chunk.labels,
                    )
                    if current_reason is not None:
                        section_exclusions[current_path] = current_reason

                node = section_nodes.get(current_path)
                if node is None:
                    section_ordinal += 1
                    stable_key = "/".join(slugify_text(part) or "section" for part in current_path)
                    stable_id = stable_uuid(f"section::{doc_family}::{stable_key}")
                    node_id = stable_uuid(f"{document_id}::section::{stable_id}")
                    node = HierarchyNode(
                        node_id=node_id,
                        stable_id=stable_id,
                        content_hash="",
                        document_id=document_id,
                        document_stable_id=document_stable_id,
                        node_type="section",
                        parent_id=parent_id,
                        title=title,
                        text="",
                        section_path=" / ".join(current_path),
                        section_titles=list(current_path),
                        page_number=chunk.page_number,
                        ordinal=section_ordinal,
                        indexable=False,
                        excluded_from_index=current_reason is not None,
                        exclusion_reason=current_reason,
                        source=chunk.source,
                        lineage=lineage.copy(),
                        lineage_ids=section_lineage_ids.copy(),
                        metadata={
                            "anchor_text": title,
                            "source_labels": list(chunk.labels),
                            "source_refs": list(chunk.source_refs),
                        },
                    )
                    section_nodes[current_path] = node
                    nodes.append(node)
                else:
                    if node.page_number is None:
                        node.page_number = chunk.page_number
                    if current_reason and not node.excluded_from_index:
                        node.excluded_from_index = True
                        node.exclusion_reason = current_reason

                parent_id = node.node_id
                lineage.append(title)
                section_lineage_ids.append(node.node_id)

            leaf_reason = _classify_toc_block(
                title=chunk.title or section_titles[-1],
                section_titles=section_titles,
                text=chunk.text,
                labels=chunk.labels,
            )
            if leaf_reason is not None:
                section_exclusions[section_titles] = leaf_reason
                section_nodes[section_titles].excluded_from_index = True
                section_nodes[section_titles].exclusion_reason = leaf_reason
                inherited_reason = leaf_reason
            elif inherited_reason is None:
                inherited_reason = _get_inherited_exclusion(section_titles, section_exclusions)

        if chunk.chunk_type == "heading":
            continue

        if not section_titles:
            section_titles = ("Unsectioned",)
            section_path = " / ".join(section_titles)
            inherited_reason = _classify_toc_block(
                title=chunk.title,
                section_titles=section_titles,
                text=chunk.text,
                labels=chunk.labels,
            )
            if section_titles not in section_nodes:
                section_ordinal += 1
                stable_id = stable_uuid(f"section::{doc_family}::unsectioned")
                node_id = stable_uuid(f"{document_id}::section::{stable_id}")
                section_node = HierarchyNode(
                    node_id=node_id,
                    stable_id=stable_id,
                    content_hash="",
                    document_id=document_id,
                    document_stable_id=document_stable_id,
                    node_type="section",
                    parent_id=document_id,
                    title="Unsectioned",
                    text="",
                    section_path=section_path,
                    section_titles=list(section_titles),
                    page_number=chunk.page_number,
                    ordinal=section_ordinal,
                    indexable=False,
                    excluded_from_index=inherited_reason is not None,
                    exclusion_reason=inherited_reason,
                    source=chunk.source,
                    lineage=[],
                    lineage_ids=[document_id],
                    metadata={"anchor_text": "Unsectioned"},
                )
                section_nodes[section_titles] = section_node
                nodes.append(section_node)
            section_lineage_ids = [document_id, section_nodes[section_titles].node_id]

        section_leaf_ordinals[section_titles] += 1
        anchor_text = _anchor_text(chunk)
        # For tables, prefer structured clean_text if available
        if chunk.chunk_type == "table" and chunk.metadata.get("table_clean_text"):
            clean_text = str(chunk.metadata.get("table_clean_text"))
        else:
            clean_text = str(chunk.metadata.get("clean_text") or normalize_text(chunk.text))
        normalized_text = normalize_text(clean_text or chunk.text)
        content_digest = sha256_hex(normalized_text.encode("utf-8")) if normalized_text else ""
        stable_id = stable_uuid(
            f"{chunk.chunk_type}::{doc_family}::{section_path}::{anchor_text}"
        )
        node_id = stable_uuid(
            "::".join(
                [
                    document_id,
                    chunk.chunk_type,
                    stable_id,
                    str(section_leaf_ordinals[section_titles]),
                    content_digest,
                ]
            )
        )
        exclusion_reason = inherited_reason or _classify_toc_block(
            title=chunk.title,
            section_titles=section_titles,
            text=chunk.text,
            labels=chunk.labels,
        )
        indexable = (
            chunk.chunk_type in {"clause", "table"}
            and bool(normalized_text)
            and exclusion_reason is None
        )
        if chunk.chunk_type == "figure":
            indexable = False

        node = HierarchyNode(
            node_id=node_id,
            stable_id=stable_id,
            content_hash=content_digest,
            document_id=document_id,
            document_stable_id=document_stable_id,
            node_type=chunk.chunk_type,
            parent_id=section_nodes[section_titles].node_id,
            title=chunk.title,
            text=chunk.text.strip(),
            section_path=section_path,
            section_titles=list(section_titles),
            page_number=chunk.page_number,
            ordinal=chunk.ordinal,
            indexable=indexable,
            excluded_from_index=exclusion_reason is not None,
            exclusion_reason=exclusion_reason,
            source=chunk.source,
            lineage=list(section_titles),
            lineage_ids=section_lineage_ids,
            metadata={
                **chunk.metadata,
                "clean_text": clean_text,
                "markdown_text": chunk.markdown_text,
                "anchor_text": anchor_text,
                "docling_path": chunk.docling_path,
                "source_labels": list(chunk.labels),
                "source_refs": list(chunk.source_refs),
            },
        )
        nodes.append(node)

        if node.node_type in {"clause", "table"} and not node.excluded_from_index and node.text:
            for depth in range(1, len(section_titles) + 1):
                section_buffers[section_titles[:depth]].append(clean_text or node.text)

    for path, section_node in section_nodes.items():
        aggregated_text = _aggregate_section_text(section_buffers.get(path, []))
        section_node.text = aggregated_text
        section_node.content_hash = (
            sha256_hex(normalize_text(aggregated_text).encode("utf-8"))
            if aggregated_text
            else ""
        )
        section_node.indexable = bool(aggregated_text) and not section_node.excluded_from_index
        section_node.metadata["descendant_text_fragments"] = len(section_buffers.get(path, []))
        section_node.metadata["clean_text"] = aggregated_text

    indexable_nodes = [
        node
        for node in nodes
        if node.node_type in {"section", "clause", "table"}
        and node.indexable
        and node.text
        and not node.excluded_from_index
    ]

    counts = Counter(node.node_type for node in nodes)
    return DocumentHierarchy(
        document_id=document_id,
        document_stable_id=document_stable_id,
        document_family=doc_family,
        content_hash=content_hash,
        nodes=nodes,
        indexable_nodes=indexable_nodes,
        metadata={
            "node_counts": dict(counts),
            "excluded_nodes": sum(1 for node in nodes if node.excluded_from_index),
            "ocr_nodes": sum(1 for node in nodes if node.source == "pytesseract"),
        },
    )


def document_family_from_filename(filename: str) -> str:
    stem = Path(filename).stem.strip()
    if not stem:
        return "document"
    simplified = _VERSION_SUFFIX_RE.sub("", stem).strip() or stem
    return slugify_text(simplified) or "document"


def _normalize_section_titles(chunk: ParsedChunk) -> tuple[str, ...]:
    titles = tuple(title.strip() for title in chunk.section_path if title and title.strip())
    if titles:
        return titles
    if chunk.chunk_type == "heading" and chunk.title:
        return (chunk.title.strip(),)
    return ()


def _anchor_text(chunk: ParsedChunk) -> str:
    if chunk.chunk_type == "section":
        return chunk.title or (chunk.section_path[-1] if chunk.section_path else "section")

    candidates = [chunk.title or "", chunk.text]
    for candidate in candidates:
        normalized = normalize_text(candidate)
        if not normalized:
            continue
        match = _CLAUSE_MARKER_RE.match(candidate)
        if match:
            return normalize_text(match.group(1))
        tokens = normalized.split()
        return " ".join(tokens[:12])

    if chunk.section_path:
        return normalize_text(chunk.section_path[-1])
    return chunk.chunk_type


def _classify_toc_block(
    *,
    title: str | None,
    section_titles: tuple[str, ...],
    text: str,
    labels: tuple[str, ...],
) -> str | None:
    normalized_title = normalize_text(title or "")
    normalized_sections = {normalize_text(part) for part in section_titles}
    normalized_labels = {normalize_text(label) for label in labels}

    if "document_index" in normalized_labels:
        return "document_index"
    if normalized_title in _TOC_TITLES or normalized_sections & _TOC_TITLES:
        return "table_of_contents"

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return None

    toc_like_lines = sum(1 for line in lines if _TOC_LINE_RE.match(line))
    if toc_like_lines >= 3 and toc_like_lines / len(lines) >= 0.5:
        return "table_of_contents"
    return None


def _get_inherited_exclusion(
    section_titles: tuple[str, ...],
    exclusions: dict[tuple[str, ...], str],
) -> str | None:
    for depth in range(1, len(section_titles) + 1):
        reason = exclusions.get(section_titles[:depth])
        if reason is not None:
            return reason
    return None


def _aggregate_section_text(parts: list[str]) -> str:
    if not parts:
        return ""
    out: list[str] = []
    size = 0
    for part in parts:
        cleaned = " ".join(part.split())
        if not cleaned:
            continue
        if size + len(cleaned) > _MAX_SECTION_TEXT:
            remaining = _MAX_SECTION_TEXT - size
            if remaining > 0:
                out.append(cleaned[:remaining].rstrip())
            break
        out.append(cleaned)
        size += len(cleaned) + 2
    return "\n\n".join(out)
