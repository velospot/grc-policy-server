from __future__ import annotations

import json
import re as _re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

import logging

# Regex helpers for section hierarchy extraction
_CLAUSE_NUM_RE = _re.compile(r'\b(\d+(?:\.\d+)+)\b')  # multi-level: "5.2.3"
_TOP_LEVEL_NUM_RE = _re.compile(r'^\s*(\d+)\b')        # top-level only: "5"

from grc_policy_server.models.schemas import (
    ActionItem,
    ChangeDetail,
    ComparisonResult,
    DocumentReference,
    GraphChangeRecord,
    GraphComparisonResult,
    GraphComparisonSummary,
    GraphNodeRef,
    GraphPropertyChange,
    GraphRelationshipChange,
    KeyDifference,
)
from grc_policy_server.services.agents.graph_explanation_agent import GraphExplanationAgent
from grc_policy_server.services.comparison.split_merge_detector import SplitMergeDetector
from grc_policy_server.services.graph.docling_graph_adapter import (
    DoclingGraphArtifact,
    DoclingGraphNode,
)
from grc_policy_server.utils.hashing import normalize_text, sha256_hex

logger = logging.getLogger(__name__)

_EMBED_MATCH_THRESHOLD = 0.85
_EMBED_REVIEW_THRESHOLD = 0.60


_VOLATILE_PROPERTY_KEYS = {
    "classification",
    "confidence",
    "extraction_confidence",
    "metadata",
    "raw_value",
    "row", "col",   # table position — non-semantic; same value can move rows on restructure
}
_COMPLIANCE_CRITICAL_TYPES = {
    "Requirement",
    "Measurement",
    "Threshold",
    "Deviation",
    "Risk",
    "Standard",
}
_CRITICAL_PROPERTY_KEYS = {
    "value",
    "unit",
    "standard_ref",
    "standard_version",
    "clause",
    "obligation",
    "fact_type",
    "name",
    "column_header",    # test condition identifier for EMC/safety/environmental tables
    "formula_display",  # formula change is compliance-critical
}

# Node types whose actual text content should be shown to auditors (not raw metadata)
_MEASUREMENT_TYPES = {"Measurement", "Threshold", "Evidence"}
_TEXT_CONTENT_TYPES = {"Requirement", "Observation", "Risk"}

# Human-readable labels for property diff display
_PROPERTY_LABELS: dict[str, str] = {
    "obligation": "Obligation",
    "standard_version": "Standard version",
    "standard_ref": "Standard reference",
    "clause": "Clause number",
    "value": "Value",
    "unit": "Unit",
    "fact_type": "Fact type",
    "name": "Name",
    "subject": "Subject",
    "action": "Action",
    "condition": "Condition",
    "object": "Object",
    "section_path": "Section path",
    "content_hash": "Content hash",
    "column_header": "Test condition",
    "formula_display": "Formula",
    "formula_latex": "Formula (LaTeX)",
}

# Display names for rationale text (lowercase natural language)
_PROPERTY_DISPLAY_NAMES: dict[str, str] = {
    "obligation": "obligation",
    "standard_version": "standard version",
    "standard_ref": "standard reference",
    "clause": "clause",
    "value": "value",
    "unit": "unit",
    "fact_type": "fact type",
    "name": "name",
    "subject": "subject",
    "action": "action",
    "column_header": "test condition",
}
_MAX_RATIONALE_PROPS = 3

# Properties hidden from the UI changes[] array — comparison-useful internally but
# meaningless or redundant for auditors seeing the output.
_UI_HIDDEN_PROPERTY_KEYS = {
    "content_hash", "_original_text", "_text",     # internal storage
    "section_path",                                  # duplicate of top-level section field
    "subject", "action", "object", "condition",     # verbose semantic extraction
    "stable_id_basis", "extraction_quality_score",  # technical metadata
    "docling_path", "source", "excluded_from_index",
    "exclusion_reason", "ordinal",
    "anchor_text", "source_labels", "source_refs",
    "domain",
    "row", "col",              # table position — structural artifact, not compliance change
    "table_title",             # display context, not a change indicator
    "row_semantic_key",        # match key, not a change indicator
}


@dataclass(frozen=True)
class _LoadedGraphs:
    comparison_id: str
    left: DoclingGraphArtifact
    right: DoclingGraphArtifact
    warnings: list[str]


class GraphArtifactStore:
    def __init__(self, *, upload_root: Path) -> None:
        self.upload_root = upload_root

    def load(self, document_id: str) -> DoclingGraphArtifact:
        doc_id = document_id.strip()
        path = self.upload_root / doc_id / "docling_graph.json"
        if not path.exists():
            raise ValueError(
                f"Document graph artifact not found for {doc_id}. "
                "Re-ingest the document to create docling_graph.json."
            )
        return DoclingGraphArtifact.model_validate_json(path.read_text(encoding="utf-8"))


class GraphTreeComparisonOrchestrator:
    """Staged graph-tree comparison.

    This is deliberately graph-first: Weaviate/embeddings are not consulted.
    The source of truth is the validated Docling graph artifact produced during
    ingestion, including compliance nodes, layout provenance, and typed edges.
    """

    def __init__(
        self,
        *,
        artifact_store: GraphArtifactStore,
        explanation_agent: GraphExplanationAgent | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.explanation_agent = explanation_agent

    async def compare(
        self,
        *,
        doc1_id: str,
        doc2_id: str,
        testing_department: str = "",
        include_unchanged: bool = False,
    ) -> GraphComparisonResult:
        final: GraphComparisonResult | None = None
        async for event in self.compare_events(
            doc1_id=doc1_id,
            doc2_id=doc2_id,
            testing_department=testing_department,
            include_unchanged=include_unchanged,
        ):
            if event.get("type") == "done":
                final = GraphComparisonResult.model_validate(event["result"])
        if final is None:
            raise RuntimeError("graph comparison did not produce a final result")
        return final

    async def compare_as_current_response(
        self,
        *,
        doc1_id: str,
        doc2_id: str,
        testing_department: str = "",
        include_unchanged: bool = False,
    ) -> ComparisonResult:
        # Load artifacts here so we can extract filenames for the summary.
        left = self.artifact_store.load(doc1_id)
        right = self.artifact_store.load(doc2_id)
        graph_result = await self.compare(
            doc1_id=doc1_id,
            doc2_id=doc2_id,
            testing_department=testing_department,
            include_unchanged=include_unchanged,
        )
        return await self.to_current_response(
            graph_result,
            testing_department=testing_department,
            doc1_filename=_doc_filename(left),
            doc2_filename=_doc_filename(right),
        )

    async def to_current_response(
        self,
        graph_result: GraphComparisonResult,
        *,
        testing_department: str = "",
        doc1_filename: str = "",
        doc2_filename: str = "",
    ) -> ComparisonResult:
        return await graph_result_to_comparison_result(
            graph_result,
            explanation_agent=self.explanation_agent,
            testing_department=testing_department,
            doc1_filename=doc1_filename,
            doc2_filename=doc2_filename,
        )

    async def key_difference_for_change(
        self,
        change: GraphChangeRecord,
        *,
        testing_department: str = "",
    ) -> KeyDifference:
        key_difference = graph_change_to_key_difference(change)
        if self.explanation_agent is None:
            return key_difference
        return await self.explanation_agent.explain_key_difference(
            change=change,
            key_difference=key_difference,
            testing_department=testing_department,
        )

    async def compare_events(
        self,
        *,
        doc1_id: str,
        doc2_id: str,
        testing_department: str = "",
        include_unchanged: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        comparison_id = str(uuid.uuid4())
        yield {
            "type": "payload",
            "comparison_id": comparison_id,
            "doc1_id": doc1_id,
            "doc2_id": doc2_id,
            "testing_department": testing_department,
            "comparison_mode": "document_graph_tree",
        }

        yield {"type": "progress", "stage": "load_document_graphs", "message": "Loading graph artifacts"}
        loaded = self._load_graphs(comparison_id=comparison_id, doc1_id=doc1_id, doc2_id=doc2_id)

        yield {"type": "progress", "stage": "validate_graphs", "message": "Validating graph invariants"}
        warnings = list(loaded.warnings)
        warnings.extend(_validate_graph(loaded.left, side="doc1"))
        warnings.extend(_validate_graph(loaded.right, side="doc2"))

        yield {"type": "progress", "stage": "align_graph_trees", "message": "Aligning compliance graph nodes"}
        aligned, unmatched_left, unmatched_right = _align_nodes(
            left=loaded.left, right=loaded.right
        )

        yield {"type": "progress", "stage": "detect_split_merge", "message": "Detecting section splits and merges"}
        split_merge = SplitMergeDetector().detect(
            unmatched_left, unmatched_right, loaded.left, loaded.right
        )
        # Nodes absorbed into SPLIT/MERGE groups are removed from the unmatched pool
        split_absorbed_left: set[str] = set()
        split_absorbed_right: set[str] = set()
        split_change_records: list[GraphChangeRecord] = []
        for group in split_merge.split_groups:
            if group.alignment_type == "SPLIT":
                src = _node_by_id(loaded.left, group.source_node_id)
                if src:
                    split_absorbed_left.add(group.source_node_id)
                    split_absorbed_right.update(group.target_node_ids)
                    split_change_records.append(_split_change_record(src, group, loaded.right))
            else:  # MERGE
                src = _node_by_id(loaded.right, group.source_node_id)
                if src:
                    split_absorbed_right.add(group.source_node_id)
                    split_absorbed_left.update(group.target_node_ids)
                    split_change_records.append(_merge_change_record(src, group, loaded.left))

        yield {
            "type": "progress",
            "stage": "diff_node_properties",
            "message": "Diffing graph node properties",
            "total": len(aligned),
        }
        changes: list[GraphChangeRecord] = list(split_change_records)
        for pair in aligned:
            change = _change_from_pair(pair, loaded.left, loaded.right)
            if change.changeType == "UNCHANGED" and not include_unchanged:
                continue
            if _has_only_hidden_changes(change) and not include_unchanged:
                continue
            changes.append(change)
            event: dict[str, Any] = {"type": "change", "change": change.model_dump(mode="json")}
            if self.explanation_agent is not None:
                key_difference = await self.key_difference_for_change(
                    change,
                    testing_department=testing_department,
                )
                event["key_difference"] = key_difference.model_dump(mode="json")
            yield event

        # Emit remaining unmatched nodes (not absorbed by split/merge) as ADDED/REMOVED
        for node in unmatched_left:
            if node.node_id not in split_absorbed_left:
                change = _single_node_change("REMOVED", node, confidence=0.95)
                if not (change.changeType == "UNCHANGED" and not include_unchanged):
                    changes.append(change)
                    yield {"type": "change", "change": change.model_dump(mode="json")}
        for node in unmatched_right:
            if node.node_id not in split_absorbed_right:
                change = _single_node_change("ADDED", node, confidence=0.95)
                if not (change.changeType == "UNCHANGED" and not include_unchanged):
                    changes.append(change)
                    yield {"type": "change", "change": change.model_dump(mode="json")}

        yield {"type": "progress", "stage": "classify_graph_findings", "message": "Classifying graph changes"}
        changes.sort(key=_change_sort_key)
        result = _result_from_changes(
            comparison_id=comparison_id,
            doc1_id=doc1_id,
            doc2_id=doc2_id,
            changes=changes,
            warnings=warnings,
        )
        yield {"type": "done", "result": result.model_dump(mode="json")}

    def _load_graphs(
        self,
        *,
        comparison_id: str,
        doc1_id: str,
        doc2_id: str,
    ) -> _LoadedGraphs:
        left = self.artifact_store.load(doc1_id)
        right = self.artifact_store.load(doc2_id)
        warnings: list[str] = []
        if _language(left) and _language(right) and _language(left) != _language(right):
            warnings.append(
                f"Document language differs: doc1={_language(left)} doc2={_language(right)}. "
                "Graph facts are compared, but narrative interpretation should be reviewed."
            )
        return _LoadedGraphs(comparison_id=comparison_id, left=left, right=right, warnings=warnings)


@dataclass(frozen=True)
class _AlignedPair:
    key: str
    left: DoclingGraphNode | None
    right: DoclingGraphNode | None
    confidence: float


def _section_grouped_index(
    artifact: DoclingGraphArtifact,
) -> dict[str, list[DoclingGraphNode]]:
    """Group compliance nodes by their section key for section-scoped comparison.

    Nodes in the same section share a clause number prefix (e.g. "5.2.3").
    Nodes with no section fall into the "_root" bucket.
    """
    groups: dict[str, list[DoclingGraphNode]] = {}
    owner_section = _node_section_from_owner(artifact)
    incoming_owner = _incoming_fact_owner(artifact)
    for node in artifact.nodes:
        if node.layer != "compliance":
            continue
        # Fact nodes inherit their owner table's section key
        if node.properties.get("fact_type"):
            sec = owner_section.get(node.node_id) or _section_key(
                str(node.properties.get("section_path") or "")
            )
        else:
            sec = _section_key(str(node.properties.get("section_path") or ""))
        groups.setdefault(sec or "_root", []).append(node)
    return groups


def _align_section_contents(
    left_nodes: list[DoclingGraphNode],
    right_nodes: list[DoclingGraphNode],
    left_artifact: DoclingGraphArtifact,
    right_artifact: DoclingGraphArtifact,
) -> tuple[list[_AlignedPair], list[DoclingGraphNode], list[DoclingGraphNode]]:
    """Align compliance nodes within a single matched section pair (Pass 1 only)."""
    owner_section_l = _node_section_from_owner(left_artifact)
    owner_section_r = _node_section_from_owner(right_artifact)
    incoming_l = _incoming_fact_owner(left_artifact)
    incoming_r = _incoming_fact_owner(right_artifact)

    left_by_key: dict[str, DoclingGraphNode] = {}
    for n in left_nodes:
        k = _node_match_key(n, incoming_owner=incoming_l, owner_section=owner_section_l)
        if k and k not in left_by_key:
            left_by_key[k] = n

    right_by_key: dict[str, DoclingGraphNode] = {}
    for n in right_nodes:
        k = _node_match_key(n, incoming_owner=incoming_r, owner_section=owner_section_r)
        if k and k not in right_by_key:
            right_by_key[k] = n

    matched = set(left_by_key) & set(right_by_key)
    pairs: list[_AlignedPair] = [
        _AlignedPair(key=k, left=left_by_key[k], right=right_by_key[k], confidence=1.0)
        for k in sorted(matched)
    ]
    unmatched_left = [left_by_key[k] for k in sorted(set(left_by_key) - matched)]
    unmatched_right = [right_by_key[k] for k in sorted(set(right_by_key) - matched)]
    return pairs, unmatched_left, unmatched_right


def _align_nodes(
    *,
    left: DoclingGraphArtifact,
    right: DoclingGraphArtifact,
) -> tuple[list[_AlignedPair], list[DoclingGraphNode], list[DoclingGraphNode]]:
    """Section-first, then content alignment.

    Pass 0: Group nodes by section key; match same-section nodes together.
    Pass 1: Within each matched section, exact deterministic key match.
    Pass 2: Embedding cosine similarity for nodes unmatched after pass 1.
    """
    left_by_section = _section_grouped_index(left)
    right_by_section = _section_grouped_index(right)
    all_section_keys = sorted(set(left_by_section) | set(right_by_section))

    pairs: list[_AlignedPair] = []
    all_unmatched_left: list[DoclingGraphNode] = []
    all_unmatched_right: list[DoclingGraphNode] = []

    for sec_key in all_section_keys:
        left_nodes = left_by_section.get(sec_key, [])
        right_nodes = right_by_section.get(sec_key, [])
        sec_pairs, sec_unmatched_l, sec_unmatched_r = _align_section_contents(
            left_nodes, right_nodes, left, right
        )
        pairs.extend(sec_pairs)
        all_unmatched_left.extend(sec_unmatched_l)
        all_unmatched_right.extend(sec_unmatched_r)

    # Pass 2: embedding-based fallback for cross-section stragglers
    embedding_pairs = _embedding_align_unmatched(all_unmatched_left, all_unmatched_right)
    absorbed_left = {p.left.node_id for p in embedding_pairs if p.left}
    absorbed_right = {p.right.node_id for p in embedding_pairs if p.right}
    pairs.extend(embedding_pairs)

    final_left_unmatched = [n for n in all_unmatched_left if n.node_id not in absorbed_left]
    final_right_unmatched = [n for n in all_unmatched_right if n.node_id not in absorbed_right]
    return pairs, final_left_unmatched, final_right_unmatched


def _embedding_align_unmatched(
    left_nodes: list[DoclingGraphNode],
    right_nodes: list[DoclingGraphNode],
) -> list[_AlignedPair]:
    """Cosine-similarity fallback alignment using LocalEmbeddingService (BGE-M3).

    Silently returns [] when the embedding service is unavailable so the pipeline
    degrades gracefully to pass-1-only alignment.
    """
    if not left_nodes or not right_nodes:
        return []
    try:
        from grc_policy_server.services.embedding.local_embedding_service import (
            LocalEmbeddingService,
        )
        embed_svc = LocalEmbeddingService()
    except Exception:
        return []

    try:
        def _node_text(n: DoclingGraphNode) -> str:
            return f"{n.title or ''} {(n.text or '')[:300]}".strip()

        left_texts = [_node_text(n) for n in left_nodes]
        right_texts = [_node_text(n) for n in right_nodes]
        left_vecs = embed_svc.embed_batch(left_texts)
        right_vecs = embed_svc.embed_batch(right_texts)
    except Exception:
        logger.debug("embedding alignment failed; using pass-1 only", exc_info=True)
        return []

    import math

    def cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0

    pairs: list[_AlignedPair] = []
    used_right: set[int] = set()

    for li, (left_node, left_vec) in enumerate(zip(left_nodes, left_vecs)):
        best_score = _EMBED_REVIEW_THRESHOLD
        best_ri = -1
        left_top = _top_section(left_node)
        for ri, right_vec in enumerate(right_vecs):
            if ri in used_right:
                continue
            # Section guard: skip cross-chapter matches when both nodes have section info
            right_top = _top_section(right_nodes[ri])
            if left_top and right_top and left_top != right_top:
                continue
            score = cosine(left_vec, right_vec)
            if score > best_score:
                best_score = score
                best_ri = ri
        if best_ri < 0:
            continue
        right_node = right_nodes[best_ri]
        used_right.add(best_ri)
        requires_review = best_score < _EMBED_MATCH_THRESHOLD
        key = f"embed::{left_node.node_id}::{right_node.node_id}"
        pairs.append(
            _AlignedPair(
                key=key,
                left=left_node,
                right=right_node,
                confidence=round(best_score, 4),
            )
        )
        if requires_review:
            logger.debug(
                "embedding alignment low-confidence %.3f: %s ↔ %s",
                best_score, left_node.title, right_node.title,
            )

    return pairs


def _node_index(artifact: DoclingGraphArtifact) -> dict[str, DoclingGraphNode]:
    result: dict[str, DoclingGraphNode] = {}
    incoming_owner = _incoming_fact_owner(artifact)
    owner_section = _node_section_from_owner(artifact)
    for node in artifact.nodes:
        if node.layer != "compliance":
            continue
        key = _node_match_key(node, incoming_owner=incoming_owner, owner_section=owner_section)
        if key and key not in result:
            result[key] = node
    return result


def _node_match_key(
    node: DoclingGraphNode,
    *,
    incoming_owner: dict[str, str],
    owner_section: dict[str, str] | None = None,
) -> str:
    props = node.properties or {}

    if props.get("fact_type"):
        # Use owner's section_key (stable across document versions) instead of stable_id.
        # Falls back to stable_id when no section is available.
        if owner_section is not None:
            owner_key = owner_section.get(node.node_id) or (
                incoming_owner.get(node.node_id) or str(node.source_node_id or "")
            )
        else:
            owner_key = incoming_owner.get(node.node_id) or str(node.source_node_id or "")
        # Prefer row_semantic_key when available (e.g. "emc:lf", "safety:contact_discharge").
        # This is stable across table restructuring: the row identifier (band, phenomenon,
        # discharge type) doesn't change when test limits change between document versions.
        # Falls back to column_header-based matching when no row key is available.
        row_semantic_key = str(props.get("row_semantic_key") or "").strip()
        if row_semantic_key:
            return "::".join([
                "fact",
                owner_key,
                row_semantic_key,
                str(props.get("fact_type") or ""),
                str(props.get("name") or ""),
            ])
        # Fallback: column-header-based match (position-agnostic)
        return "::".join(
            [
                "fact",
                owner_key,
                normalize_text(str(props.get("column_header") or "")),
                str(props.get("fact_type") or ""),
                str(props.get("name") or ""),
            ]
        )

    if node.ontology_type == "Standard":
        return f"standard::{normalize_text(str(props.get('standard_ref') or node.title))}"

    # For all other nodes: use the deepest clause number extracted from section_path
    # as the primary discriminator.  This is stable across document versions that share
    # the same section numbering (e.g. TL_81000_2018 vs TL_81000_2021).
    section_path = str(props.get("section_path") or "")
    sec_key = _section_key(section_path)
    clause = normalize_text(str(props.get("clause") or ""))
    title = normalize_text(node.title or "")
    stable = node.stable_id or ""
    content_hash = str(props.get("content_hash") or "")

    # Priority: explicit clause → section clause number → stable_id → content_hash
    discriminator = clause or sec_key or stable
    if not discriminator and content_hash:
        return f"node::{node.ontology_type or node.label}::hash::{content_hash}"
    return f"node::{node.ontology_type or node.label}::{discriminator}::{title}"


def _incoming_fact_owner(artifact: DoclingGraphArtifact) -> dict[str, str]:
    node_by_id = {node.node_id: node for node in artifact.nodes}
    result: dict[str, str] = {}
    for edge in artifact.edges:
        if edge.rel_type != "HAS_FACT":
            continue
        owner = node_by_id.get(edge.from_node)
        if owner is None:
            continue
        result[edge.to_node] = owner.stable_id or owner.node_id
    return result


def _change_from_pair(
    pair: _AlignedPair,
    left_artifact: DoclingGraphArtifact,
    right_artifact: DoclingGraphArtifact,
) -> GraphChangeRecord:
    if pair.left is None and pair.right is None:
        raise ValueError("aligned pair has no nodes")
    if pair.left is None:
        node = pair.right
        return _single_node_change("ADDED", node, confidence=pair.confidence)
    if pair.right is None:
        return _single_node_change("REMOVED", pair.left, confidence=pair.confidence)

    property_changes = _property_changes(pair.left.properties, pair.right.properties)
    relationship_changes = _relationship_changes(pair.left, pair.right, left_artifact, right_artifact)
    change_type = "MODIFIED" if property_changes or relationship_changes else "UNCHANGED"
    severity = _severity(pair.left, pair.right, property_changes, relationship_changes, change_type)
    requires_review = _requires_review(pair.left, pair.right, property_changes, confidence=pair.confidence)
    return GraphChangeRecord(
        changeId=_change_id(pair.key, change_type),
        changeType=change_type,
        severity=severity,
        layer="compliance",
        ontologyType=pair.left.ontology_type or pair.right.ontology_type,
        title=pair.right.title or pair.left.title,
        sectionPath=str(pair.right.properties.get("section_path") or pair.left.properties.get("section_path") or ""),
        doc1Node=_node_ref(pair.left),
        doc2Node=_node_ref(pair.right),
        propertyChanges=property_changes,
        relationshipChanges=relationship_changes,
        confidence=pair.confidence,
        requiresHumanReview=requires_review,
        rationale=_rationale(change_type, severity, pair.left, pair.right, property_changes, relationship_changes),
    )


def _single_node_change(
    change_type: str,
    node: DoclingGraphNode,
    *,
    confidence: float,
) -> GraphChangeRecord:
    severity = "high" if node.ontology_type in _COMPLIANCE_CRITICAL_TYPES else "medium"
    return GraphChangeRecord(
        changeId=_change_id(node.node_id, change_type),
        changeType=change_type,  # type: ignore[arg-type]
        severity=severity,
        layer=node.layer,
        ontologyType=node.ontology_type,
        title=node.title,
        sectionPath=str(node.properties.get("section_path") or ""),
        doc1Node=_node_ref(node) if change_type == "REMOVED" else None,
        doc2Node=_node_ref(node) if change_type == "ADDED" else None,
        confidence=confidence,
        requiresHumanReview=node.ontology_type in {"Measurement", "Threshold", "Standard"},
        rationale=f"{node.ontology_type or node.label} node was {change_type.lower()} in the document graph.",
    )


def _property_changes(left: dict[str, Any], right: dict[str, Any]) -> list[GraphPropertyChange]:
    result: list[GraphPropertyChange] = []
    left_flat = _flatten_properties(left)
    right_flat = _flatten_properties(right)
    for key in sorted(set(left_flat) | set(right_flat)):
        if _skip_property_path(key):
            continue
        old = left_flat.get(key)
        new = right_flat.get(key)
        if old != new:
            result.append(GraphPropertyChange(path=key, oldValue=old, newValue=new))
    return result


def _relationship_changes(
    left: DoclingGraphNode,
    right: DoclingGraphNode,
    left_artifact: DoclingGraphArtifact,
    right_artifact: DoclingGraphArtifact,
) -> list[GraphRelationshipChange]:
    left_edges = _edge_signature_set(left, left_artifact)
    right_edges = _edge_signature_set(right, right_artifact)
    changes: list[GraphRelationshipChange] = []
    for rel_type, target_key, target_label in sorted(left_edges - right_edges):
        changes.append(
            GraphRelationshipChange(
                changeType="REMOVED",
                relType=rel_type,
                targetKey=target_key,
                targetLabel=target_label,
            )
        )
    for rel_type, target_key, target_label in sorted(right_edges - left_edges):
        changes.append(
            GraphRelationshipChange(
                changeType="ADDED",
                relType=rel_type,
                targetKey=target_key,
                targetLabel=target_label,
            )
        )
    return changes


def _edge_signature_set(
    node: DoclingGraphNode,
    artifact: DoclingGraphArtifact,
) -> set[tuple[str, str, str]]:
    node_by_id = {item.node_id: item for item in artifact.nodes}
    owner = _incoming_fact_owner(artifact)
    owner_sec = _node_section_from_owner(artifact)
    result: set[tuple[str, str, str]] = set()
    for edge in artifact.edges:
        if edge.from_node != node.node_id:
            continue
        if edge.rel_type in {"SOURCED_FROM", "HAS_FACT"}:
            continue
        target = node_by_id.get(edge.to_node)
        if target is None:
            continue
        result.add((edge.rel_type, _node_match_key(target, incoming_owner=owner, owner_section=owner_sec), target.label))
    return result


def _flatten_properties(payload: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            result.update(_flatten_properties(value, path))
        elif isinstance(value, list):
            result[path] = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        else:
            result[path] = value
    return result


def _skip_property_path(path: str) -> bool:
    return any(part in _VOLATILE_PROPERTY_KEYS for part in path.split("."))


def _severity(
    left: DoclingGraphNode,
    right: DoclingGraphNode,
    property_changes: list[GraphPropertyChange],
    relationship_changes: list[GraphRelationshipChange],
    change_type: str,
) -> str:
    ontology_type = right.ontology_type or left.ontology_type
    if change_type in {"ADDED", "REMOVED"} and ontology_type in _COMPLIANCE_CRITICAL_TYPES:
        return "high"
    if ontology_type in _COMPLIANCE_CRITICAL_TYPES:
        for change in property_changes:
            if change.path.split(".")[-1] in _CRITICAL_PROPERTY_KEYS:
                return "high"
        if relationship_changes:
            return "medium"
    if property_changes:
        return "medium"
    if relationship_changes:
        return "medium"
    return "low"


def _requires_review(
    left: DoclingGraphNode,
    right: DoclingGraphNode,
    property_changes: list[GraphPropertyChange],
    *,
    confidence: float,
) -> bool:
    if confidence < _EMBED_MATCH_THRESHOLD:
        return True
    ontology_type = right.ontology_type or left.ontology_type
    if ontology_type in {"Measurement", "Threshold", "Standard"} and property_changes:
        return True
    if any(change.path.endswith("standard_version") and "unknown" in {str(change.oldValue), str(change.newValue)} for change in property_changes):
        return True
    return False


def _rationale(
    change_type: str,
    severity: str,
    left: DoclingGraphNode,
    right: DoclingGraphNode,
    property_changes: list[GraphPropertyChange],
    relationship_changes: list[GraphRelationshipChange],
) -> str:
    ontology_type = right.ontology_type or left.ontology_type or right.label or left.label
    if change_type == "UNCHANGED":
        return f"{ontology_type} is unchanged."

    section = str(
        right.properties.get("section_path") or left.properties.get("section_path") or ""
    ).strip()
    section_str = f" Section: {section}." if section and section != "Unknown Section" else ""

    # Prioritize changes to critical compliance properties
    critical = [pc for pc in property_changes if pc.path.split(".")[-1] in _CRITICAL_PROPERTY_KEYS]
    other = [pc for pc in property_changes if pc.path.split(".")[-1] not in _CRITICAL_PROPERTY_KEYS]
    ordered = critical + other

    prop_phrases: list[str] = []
    for pc in ordered[:_MAX_RATIONALE_PROPS]:
        field = pc.path.split(".")[-1]
        display = _PROPERTY_DISPLAY_NAMES.get(field, field.replace("_", " "))
        old_s = f"'{pc.oldValue}'" if pc.oldValue is not None else "absent"
        new_s = f"'{pc.newValue}'" if pc.newValue is not None else "absent"
        prop_phrases.append(f"{display} {old_s} → {new_s}")

    if prop_phrases:
        prop_str = ", ".join(prop_phrases)
        remainder = len(property_changes) - min(len(ordered), _MAX_RATIONALE_PROPS)
        suffix = f" (+{remainder} more)" if remainder > 0 else ""
        return f"{severity.upper()} {ontology_type}: {prop_str}{suffix}.{section_str}"

    if relationship_changes:
        rel_types = ", ".join({rc.relType for rc in relationship_changes[:2]})
        return (
            f"{severity.upper()} {ontology_type}: "
            f"{len(relationship_changes)} relationship change(s) ({rel_types}).{section_str}"
        )

    return f"{severity.upper()} {ontology_type} graph presence changed.{section_str}"


def _node_ref(node: DoclingGraphNode) -> GraphNodeRef:
    props = dict(node.properties)
    # _original_text flows through node.properties from ingestion (original casing/punctuation).
    # Keep _text as a fallback for old artifacts that predate _original_text storage.
    if node.text and "_text" not in props:
        props["_text"] = node.text[:500]
    return GraphNodeRef(
        nodeId=node.node_id,
        stableId=node.stable_id,
        layer=node.layer,
        label=node.label,
        ontologyType=node.ontology_type,
        title=node.title,
        sectionPath=str(node.properties.get("section_path") or ""),
        page=node.page,
        sourceNodeId=node.source_node_id,
        properties=props,
    )


def _result_from_changes(
    *,
    comparison_id: str,
    doc1_id: str,
    doc2_id: str,
    changes: list[GraphChangeRecord],
    warnings: list[str],
) -> GraphComparisonResult:
    added = sum(1 for change in changes if change.changeType == "ADDED")
    removed = sum(1 for change in changes if change.changeType == "REMOVED")
    modified = sum(1 for change in changes if change.changeType == "MODIFIED")
    unchanged = sum(1 for change in changes if change.changeType == "UNCHANGED")
    return GraphComparisonResult(
        comparisonId=comparison_id,
        doc1Id=doc1_id,
        doc2Id=doc2_id,
        summary=GraphComparisonSummary(
            totalChanges=added + removed + modified,
            added=added,
            removed=removed,
            modified=modified,
            unchanged=unchanged,
            highSeverity=sum(1 for change in changes if change.severity == "high"),
            mediumSeverity=sum(1 for change in changes if change.severity == "medium"),
            lowSeverity=sum(1 for change in changes if change.severity == "low"),
            requiresHumanReview=any(change.requiresHumanReview for change in changes),
        ),
        changes=changes,
        warnings=warnings,
    )


async def graph_result_to_comparison_result(
    result: GraphComparisonResult,
    *,
    explanation_agent: GraphExplanationAgent | None = None,
    testing_department: str = "",
    doc1_filename: str = "",
    doc2_filename: str = "",
) -> ComparisonResult:
    key_differences: list[KeyDifference] = []
    for change in result.changes:
        key_difference = graph_change_to_key_difference(change)
        if explanation_agent is not None:
            key_difference = await explanation_agent.explain_key_difference(
                change=change,
                key_difference=key_difference,
                testing_department=testing_department,
            )
        key_differences.append(key_difference)
    summary = _comparison_summary_text(result, doc1_filename=doc1_filename, doc2_filename=doc2_filename)
    warnings = list(result.warnings)
    warnings.append(
        "Graph-first comparison: derived from docling_graph.json compliance graph artifacts; Weaviate not used."
    )
    return ComparisonResult(
        summary=summary,
        keyDifferences=key_differences,
        actionPlan=_action_plan_from_graph_changes(result.changes),
        followUpQuestions=_followups_from_graph_changes(result.changes),
        comparisonMode="auditor_grade",
        requireHumanReview=result.summary.requiresHumanReview,
        hiddenDiffsCount=0,
        warnings=warnings,
        suppressedDiffsCount=0,
        skippedSections=[],
    )


def graph_change_to_key_difference(change: GraphChangeRecord) -> KeyDifference:
    change_details: list[ChangeDetail] = []
    for prop_change in change.propertyChanges:
        field = prop_change.path.split(".")[-1]
        if field in _UI_HIDDEN_PROPERTY_KEYS:
            continue  # skip internal/technical properties irrelevant to auditors
        label = _PROPERTY_LABELS.get(field, field.replace("_", " ").title())
        old_str = f"'{prop_change.oldValue}'" if prop_change.oldValue is not None else "not set"
        new_str = f"'{prop_change.newValue}'" if prop_change.newValue is not None else "not set"
        change_details.append(
            ChangeDetail(
                type="modified",
                text=f"{label}: {old_str} → {new_str}",
                oldValue=_string_value(prop_change.oldValue),
                newValue=_string_value(prop_change.newValue),
                location=change.sectionPath or None,
            )
        )
    for rel_change in change.relationshipChanges:
        change_details.append(
            ChangeDetail(
                type="added" if rel_change.changeType == "ADDED" else "removed",
                text=f"{rel_change.relType} {rel_change.targetLabel or rel_change.targetKey}",
                location=change.sectionPath or None,
            )
        )

    return KeyDifference(
        changeType=change.changeType if change.changeType != "UNCHANGED" else "MODIFIED",
        section=_display_section(change.sectionPath or change.title or "Unknown Section"),
        doc1Content=_graph_node_content(change.doc1Node),
        doc2Content=_graph_node_content(change.doc2Node),
        impact=change.rationale or _impact_from_change(change),
        changeSeverity=change.severity,
        doc1Reference=_document_reference(change.doc1Node),
        doc2Reference=_document_reference(change.doc2Node),
        nodeType=change.ontologyType or change.layer or "compliance",
        changes=change_details,
        requiresHumanReview=change.requiresHumanReview,
        severityConfidence=change.confidence,
        complianceExplanation=change.rationale,
        markdownDiffSummary=_default_markdown_diff(change),
    )


def _comparison_summary_text(
    result: GraphComparisonResult,
    *,
    doc1_filename: str = "",
    doc2_filename: str = "",
) -> str:
    name1 = doc1_filename or result.doc1Id
    name2 = doc2_filename or result.doc2Id
    summary = result.summary
    if summary.totalChanges == 0:
        return f"No compliance graph differences detected between '{name1}' and '{name2}'."
    return (
        f"Graph-tree comparison of '{name1}' vs '{name2}': "
        f"{summary.totalChanges} compliance graph change(s) detected "
        f"({summary.added} added, {summary.removed} removed, {summary.modified} modified). "
        f"Severity breakdown: {summary.highSeverity} high, "
        f"{summary.mediumSeverity} medium, {summary.lowSeverity} low."
    )


def _action_plan_from_graph_changes(changes: list[GraphChangeRecord]) -> list[ActionItem]:
    if not changes:
        return [
            ActionItem(
                priority="low",
                action="No graph-level compliance action required from this comparison.",
                timeline="Next review cycle",
                owner="Compliance engineer",
            )
        ]
    high = [change for change in changes if change.severity == "high"]
    if high:
        return [
            ActionItem(
                priority="high",
                action="Review high-severity compliance graph changes and verify evidence chains before release.",
                timeline="Before approval",
                owner="Compliance engineer",
            )
        ]
    return [
        ActionItem(
            priority="medium",
            action="Review modified graph nodes and confirm whether procedure or test evidence updates are needed.",
            timeline="Before next audit package",
            owner="Document owner",
        )
    ]


def _followups_from_graph_changes(changes: list[GraphChangeRecord]) -> list[str]:
    if not changes:
        return ["Are the two graph artifacts generated from the intended document revisions?"]
    questions: list[str] = []
    for change in changes[:4]:
        label = change.ontologyType or "graph node"
        section = change.sectionPath or change.title or "the affected section"
        questions.append(f"Does the {label} change in '{section}' affect test evidence or retest scope?")
    return questions


def _impact_from_change(change: GraphChangeRecord) -> str:
    label = change.ontologyType or change.layer
    return f"{change.changeType.lower()} {label} graph change detected."


def _display_section(path: str) -> str:
    """Return the deepest meaningful segment of a section path for UI display.

    "9 / 9.2 / 9.2.4 Test levels" → "9.2.4 Test levels"
    "Unknown Section"              → "Unknown Section"
    """
    if not path or path == "Unknown Section":
        return path or "Unknown Section"
    parts = [p.strip() for p in path.split("/") if p.strip()]
    if not parts:
        return path
    for part in reversed(parts):
        if _re.search(r"[A-Za-zÄÖÜäöüß]{3,}", part):
            return part
    return parts[-1]


def _default_markdown_diff(change: GraphChangeRecord) -> str | None:
    """Generate a minimal markdown diff summary when LLM is unavailable."""
    section = _display_section(change.sectionPath or change.title or "")
    if change.changeType == "ADDED":
        return f"**Added** {change.ontologyType or 'node'}" + (f" in *{section}*" if section else "") + "."
    if change.changeType == "REMOVED":
        return f"**Removed** {change.ontologyType or 'node'}" + (f" from *{section}*" if section else "") + "."
    user_changes = [
        pc for pc in change.propertyChanges
        if pc.path.split(".")[-1] in _CRITICAL_PROPERTY_KEYS
    ][:2]
    if not user_changes:
        return None
    lines = ["**Changes:**"]
    for pc in user_changes:
        field = _PROPERTY_LABELS.get(pc.path.split(".")[-1], pc.path.split(".")[-1])
        lines.append(f"- {field}: ~~{pc.oldValue}~~ → **{pc.newValue}**")
    return "\n".join(lines)


_TEXT_TRUNCATE = 400


def _graph_node_content(node: GraphNodeRef | None) -> str | None:
    if node is None:
        return None
    properties = node.properties or {}
    ontology = node.ontologyType or ""

    if ontology in _MEASUREMENT_TYPES:
        # Show: "[Table caption] parameter — value unit (fact_type)"
        value = properties.get("value")
        unit = str(properties.get("unit") or "").strip()
        fact_type = str(properties.get("fact_type") or "").strip()
        table_title = str(properties.get("table_title") or "").strip()
        meas_parts = []
        if value is not None:
            meas_parts.append(f"{value} {unit}".strip())
        if fact_type:
            meas_parts.append(f"({fact_type})")
        meas = " ".join(p for p in meas_parts if p)
        label = node.title or ""
        content = f"{label} — {meas}" if label and meas else (label or meas)
        if table_title:
            return f"[{table_title}] {content}" if content else f"[{table_title}]"
        return content or node.label

    if ontology in _TEXT_CONTENT_TYPES:
        # Formula takes priority — show human-readable form of LaTeX equations
        formula_display = str(properties.get("formula_display") or "").strip()
        if formula_display:
            return f"Formula: {formula_display}"
        # Prefer original-case text; fall back to lowercased _text for old artifacts
        text = str(properties.get("_original_text") or properties.get("_text") or "").strip()
        if text:
            return text[:_TEXT_TRUNCATE] + ("…" if len(text) > _TEXT_TRUNCATE else "")
        # Fallback: title + obligation
        obligation = str(properties.get("obligation") or "").strip()
        return f"{node.title} [{obligation}]" if obligation else node.title or node.label

    if ontology == "Section":
        return _display_section(node.sectionPath or node.title or "") or node.label

    # Standard and generic fallback
    std_ref = properties.get("standard_ref") or ""
    std_ver = str(properties.get("standard_version") or "").strip()
    parts = [
        node.title,
        f"ref={std_ref}" if std_ref else "",
        f"v{std_ver}" if std_ver and std_ver != "unknown" else "",
    ]
    return " ".join(p for p in parts if p) or node.label


def _document_reference(node: GraphNodeRef | None) -> DocumentReference | None:
    if node is None:
        return None
    props = node.properties or {}
    # Hash original source text for citation authenticity; fall back to properties JSON.
    orig = str(props.get("_original_text") or props.get("_text") or "").strip()
    text_hash = (
        sha256_hex(orig.encode("utf-8"))
        if orig
        else sha256_hex(json.dumps(props, sort_keys=True, default=str).encode("utf-8"))
    )
    return DocumentReference(
        section=node.sectionPath or node.title or "Unknown Section",
        page=int(node.page or 0),
        sourceText=_graph_node_content(node) or "",
        nodeId=node.nodeId,
        textHash=text_hash,
    )


def _string_value(value: object | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _validate_graph(artifact: DoclingGraphArtifact, *, side: str) -> list[str]:
    warnings: list[str] = []
    node_ids = {node.node_id for node in artifact.nodes}
    compliance_nodes = [node for node in artifact.nodes if node.layer == "compliance"]
    sourced = {edge.from_node for edge in artifact.edges if edge.rel_type == "SOURCED_FROM"}
    missing_source = [
        node.node_id
        for node in compliance_nodes
        if node.ontology_type != "Standard" and node.node_id not in sourced
    ]
    if missing_source:
        warnings.append(f"{side}: {len(missing_source)} compliance nodes lack SOURCED_FROM traceability")
    dangling_edges = [
        edge for edge in artifact.edges if edge.from_node not in node_ids or edge.to_node not in node_ids
    ]
    if dangling_edges:
        warnings.append(f"{side}: {len(dangling_edges)} graph edges reference missing nodes")
    return warnings


def _language(artifact: DoclingGraphArtifact) -> str:
    for node in artifact.nodes:
        if node.layer == "meta" and node.label == "Language":
            return node.language or node.title
    return ""


def _section_key(section_path: str) -> str:
    """Extract a stable clause number from a section_path string.

    Examples:
      "5 / 5.2 / 5.2.3 Test procedure" → "5.2.3"
      "3 / 3.1 Scope"                  → "3.1"
      "Unsectioned"                     → ""
    """
    text = str(section_path or "")
    matches = _CLAUSE_NUM_RE.findall(text)
    if matches:
        return matches[-1]  # deepest dotted clause number
    m = _TOP_LEVEL_NUM_RE.search(text)
    return m.group(1) if m else ""


def _top_section(node: DoclingGraphNode) -> str:
    """Return the top-level section number (e.g. '5') for section-guard filtering."""
    sp = str(node.properties.get("section_path") or "")
    m = _TOP_LEVEL_NUM_RE.search(sp)
    return m.group(1) if m else ""


def _node_section_from_owner(artifact: DoclingGraphArtifact) -> dict[str, str]:
    """Map fact node_id → owner table's section_key for stable cross-document fact matching.

    Fact nodes (table cells) previously keyed on owner stable_id which differs
    per document.  Using the owner's section clause number is document-version stable.
    """
    node_by_id = {n.node_id: n for n in artifact.nodes}
    result: dict[str, str] = {}
    for edge in artifact.edges:
        if edge.rel_type != "HAS_FACT":
            continue
        owner = node_by_id.get(edge.from_node)
        if owner is None:
            continue
        sec = _section_key(str(owner.properties.get("section_path") or ""))
        result[edge.to_node] = sec or (owner.stable_id or owner.node_id)
    return result


def _doc_filename(artifact: DoclingGraphArtifact) -> str:
    """Return the original filename from the meta Document node, fallback to document_id."""
    for node in artifact.nodes:
        if node.layer == "meta" and node.label == "Document":
            return node.title or node.properties.get("filename") or artifact.document_id
    return artifact.document_id


def _is_trivial_section_diff(change: GraphChangeRecord) -> bool:
    """Legacy: Section-only trivial filter (superseded by _has_only_hidden_changes)."""
    if change.ontologyType != "Section" or change.changeType != "MODIFIED":
        return False
    if change.severity not in {"low", "medium"}:
        return False
    critical = {pc.path.split(".")[-1] for pc in change.propertyChanges} & _CRITICAL_PROPERTY_KEYS
    return not critical


_ALL_HIDDEN_KEYS = _UI_HIDDEN_PROPERTY_KEYS | _VOLATILE_PROPERTY_KEYS


def _has_only_hidden_changes(change: GraphChangeRecord) -> bool:
    """Return True when a MODIFIED node has no user-visible semantic changes.

    All property changes are internal metadata — no compliance-relevant field
    (value, unit, obligation, standard_ref, clause, fact_type, column_header, name)
    changed. These nodes should be skipped to avoid flooding auditors with noise.
    """
    if change.changeType != "MODIFIED":
        return False
    # If there are relationship changes, keep the record (graph structure may matter)
    if change.relationshipChanges:
        return False
    if not change.propertyChanges:
        return True  # UNCHANGED equivalent
    for pc in change.propertyChanges:
        field = pc.path.split(".")[-1]
        if field in _CRITICAL_PROPERTY_KEYS:
            return False   # genuine semantic change
        if field not in _ALL_HIDDEN_KEYS:
            return False   # unknown field — don't suppress
    return True


def _change_id(key: str, change_type: str) -> str:
    return sha256_hex(f"{change_type}:{key}".encode("utf-8"))[:16]


def _change_sort_key(change: GraphChangeRecord) -> tuple[int, str, str]:
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    return (
        severity_rank.get(change.severity, 9),
        change.sectionPath or "",
        change.title or "",
    )


def _node_by_id(artifact: DoclingGraphArtifact, node_id: str) -> DoclingGraphNode | None:
    for node in artifact.nodes:
        if node.node_id == node_id:
            return node
    return None


def _split_change_record(
    source: DoclingGraphNode,
    group: Any,
    right_artifact: DoclingGraphArtifact,
) -> GraphChangeRecord:
    target_titles = [
        n.title for nid in group.target_node_ids
        if (n := _node_by_id(right_artifact, nid)) is not None
    ]
    return GraphChangeRecord(
        changeId=_change_id(source.node_id, "SPLIT"),
        changeType="MODIFIED",
        severity="medium",
        layer="compliance",
        ontologyType=source.ontology_type,
        title=source.title,
        sectionPath=str(source.properties.get("section_path") or ""),
        doc1Node=_node_ref(source),
        doc2Node=None,
        propertyChanges=[],
        relationshipChanges=[],
        confidence=group.confidence,
        requiresHumanReview=group.review_required,
        rationale=(
            f"SPLIT detected: section '{source.title}' was split into "
            f"{len(group.target_node_ids)} sections: {', '.join(t for t in target_titles if t)}."
        ),
    )


def _merge_change_record(
    target: DoclingGraphNode,
    group: Any,
    left_artifact: DoclingGraphArtifact,
) -> GraphChangeRecord:
    source_titles = [
        n.title for nid in group.target_node_ids
        if (n := _node_by_id(left_artifact, nid)) is not None
    ]
    return GraphChangeRecord(
        changeId=_change_id(target.node_id, "MERGE"),
        changeType="MODIFIED",
        severity="medium",
        layer="compliance",
        ontologyType=target.ontology_type,
        title=target.title,
        sectionPath=str(target.properties.get("section_path") or ""),
        doc1Node=None,
        doc2Node=_node_ref(target),
        propertyChanges=[],
        relationshipChanges=[],
        confidence=group.confidence,
        requiresHumanReview=group.review_required,
        rationale=(
            f"MERGE detected: {len(group.target_node_ids)} sections "
            f"({', '.join(t for t in source_titles if t)}) were merged into '{target.title}'."
        ),
    )
