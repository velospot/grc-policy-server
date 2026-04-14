"""
Canonicalization service — converts raw extractor output into stable CanonicalNode objects.
This is the boundary between the extractor world and the domain world.

Design rules:
- Pure transformation: no database, no network, no side effects.
- Callers own persistence; this service only produces the canonical objects.
- Delegates to canonical_models.py for the authoritative data shapes —
  it does NOT duplicate that logic; it provides the clean named entry point.
"""
from __future__ import annotations

import logging
from typing import Any

from grc_policy_server.models.domain import NodeType, SuppressionReason
from grc_policy_server.services.documents.canonical_models import (
    CanonicalNode,
    canonical_nodes_from_hierarchy,
)

log = logging.getLogger(__name__)

# Labels that signal content that should be suppressed from comparison.
_SUPPRESSED_LABELS: frozenset[str] = frozenset(
    {
        "toc",
        "table_of_contents",
        "index",
        "page_header",
        "page_footer",
        "page_number",
        "footnote_marker",
    }
)

_SUPPRESSION_REASON_MAP: dict[str, SuppressionReason] = {
    "toc": SuppressionReason.TOC,
    "table_of_contents": SuppressionReason.TOC,
    "page_header": SuppressionReason.HEADER_FOOTER,
    "page_footer": SuppressionReason.HEADER_FOOTER,
    "page_number": SuppressionReason.PAGE_NUMBER,
}

_NODE_TYPE_MAP: dict[str, NodeType] = {
    "section_header": NodeType.HEADING,
    "title": NodeType.HEADING,
    "heading": NodeType.HEADING,
    "table": NodeType.TABLE,
    "formula": NodeType.FORMULA,
    "figure": NodeType.FIGURE,
    "list_item": NodeType.LIST_ITEM,
    "footnote": NodeType.FOOTNOTE,
    "definition": NodeType.DEFINITION,
    "paragraph": NodeType.PARAGRAPH,
    "clause": NodeType.CLAUSE,
}


class CanonicalizationService:
    """
    Adapts raw extractor output into CanonicalNode lists.

    Two entry points:
    - canonicalize_hierarchy(): for Docling hierarchy dicts (primary pipeline).
    - canonicalize_node(): for a single raw record (e.g. backfill, tests).

    The service does not persist anything. Callers pass results to
    CanonicalDocumentStore.save_nodes() or equivalent.
    """

    def canonicalize_hierarchy(
        self,
        hierarchy: dict[str, Any],
        *,
        version_id: str = "1.0",
    ) -> list[CanonicalNode]:
        """
        Convert a raw Docling hierarchy dict into a flat list of CanonicalNodes.
        Delegates to canonical_nodes_from_hierarchy() — the single source of truth
        for field mapping. Returns empty list on malformed input rather than raising.
        """
        try:
            nodes = canonical_nodes_from_hierarchy(hierarchy, version_id=version_id)
        except Exception:
            log.exception(
                "canonicalize_hierarchy_failed version_id=%s", version_id
            )
            return []

        suppressed = sum(1 for n in nodes if _is_suppressed(n))
        log.info(
            "canonicalize_hierarchy_complete version_id=%s total=%d suppressed=%d",
            version_id,
            len(nodes),
            suppressed,
        )
        return nodes

    def canonicalize_node(
        self,
        record: dict[str, Any],
        *,
        version_id: str = "1.0",
    ) -> CanonicalNode:
        """Convert a single raw hierarchy record into a CanonicalNode."""
        return CanonicalNode.from_hierarchy_record(record, version_id=version_id)

    def classify_suppression(self, label: str, text: str) -> SuppressionReason | None:
        """
        Return a SuppressionReason if the label/text combination should be excluded
        from comparison, or None if the node is normative.
        """
        label_lower = label.strip().lower()
        if label_lower in _SUPPRESSED_LABELS:
            return _SUPPRESSION_REASON_MAP.get(label_lower, SuppressionReason.BOILERPLATE)
        if len(text.strip()) < 5:
            return SuppressionReason.OCR_FRAGMENT
        return None

    def infer_node_type(self, label: str) -> NodeType:
        """Map an extractor label string to the canonical NodeType enum."""
        return _NODE_TYPE_MAP.get(label.strip().lower(), NodeType.PARAGRAPH)


def _is_suppressed(node: CanonicalNode) -> bool:
    """Helper: a node is considered suppressed when its source_kind indicates exclusion."""
    return node.source_kind not in {"body", "table", "formula", "figure", "definition"}
