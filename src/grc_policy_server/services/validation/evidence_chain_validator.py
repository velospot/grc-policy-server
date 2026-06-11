from __future__ import annotations

import logging
from pydantic import BaseModel

from grc_policy_server.services.graph.docling_graph_adapter import DoclingGraphArtifact, DoclingGraphNode

logger = logging.getLogger(__name__)

# Minimum ratio of nodes with complete evidence chains before review is required.
_COVERAGE_THRESHOLD = 0.80
# Minimum compliance nodes to consider a document parseable.
# Only zero-node graphs are fatal — a single-requirement document is valid.
MIN_COMPLIANCE_NODES = 1


class EvidenceChainReport(BaseModel):
    chain_status: str  # "COMPLETE" | "PARTIAL" | "INCOMPLETE"
    coverage_pct: float
    incomplete_node_ids: list[str]
    review_required: bool


class EvidenceChainValidator:
    """Validate that compliance nodes form complete Measurement→Threshold→Standard chains.

    Does not halt the pipeline — callers route to review when review_required is True.
    """

    def validate(self, artifact: DoclingGraphArtifact) -> EvidenceChainReport:
        compliance_nodes = [n for n in artifact.nodes if n.layer == "compliance"]
        if not compliance_nodes:
            return EvidenceChainReport(
                chain_status="INCOMPLETE",
                coverage_pct=0.0,
                incomplete_node_ids=[],
                review_required=True,
            )

        # Build edge index: node_id → set of outgoing rel_types
        outgoing: dict[str, set[str]] = {}
        node_by_id: dict[str, DoclingGraphNode] = {n.node_id: n for n in artifact.nodes}
        for edge in artifact.edges:
            outgoing.setdefault(edge.from_node, set()).add(edge.rel_type)

        # Build target-type index for edges: edge.to_node → ontology_type
        target_type: dict[str, str] = {}
        for edge in artifact.edges:
            target = node_by_id.get(edge.to_node)
            if target and target.ontology_type:
                target_type[edge.to_node] = target.ontology_type

        candidate_nodes: list[DoclingGraphNode] = []
        incomplete: list[str] = []

        for node in compliance_nodes:
            otype = node.ontology_type or ""
            if otype == "Standard":
                candidate_nodes.append(node)
                version = (node.properties or {}).get("standard_version", "unknown")
                if version == "unknown":
                    incomplete.append(node.node_id)
            elif otype == "Measurement":
                candidate_nodes.append(node)
                rels = outgoing.get(node.node_id, set())
                # Must have at least one edge leading to a Threshold node
                has_threshold_link = any(
                    r in rels for r in ("HAS_FACT", "EVALUATED_AGAINST", "HAS_LIMIT")
                ) or self._has_target_type(artifact, node.node_id, "Threshold")
                if not has_threshold_link:
                    incomplete.append(node.node_id)
            elif otype == "Requirement":
                candidate_nodes.append(node)
                rels = outgoing.get(node.node_id, set())
                has_standard_link = any(
                    r in rels for r in ("PART_OF", "REFERENCES_STANDARD")
                )
                if not has_standard_link:
                    incomplete.append(node.node_id)

        total = len(candidate_nodes)
        if total == 0:
            return EvidenceChainReport(
                chain_status="COMPLETE",
                coverage_pct=1.0,
                incomplete_node_ids=[],
                review_required=False,
            )

        complete_count = total - len(incomplete)
        coverage = complete_count / total

        if coverage >= 1.0:
            status = "COMPLETE"
        elif coverage >= _COVERAGE_THRESHOLD:
            status = "PARTIAL"
        else:
            status = "INCOMPLETE"

        review_required = coverage < _COVERAGE_THRESHOLD

        logger.info(
            "evidence_chain coverage=%.1f%% status=%s incomplete=%d/%d",
            coverage * 100,
            status,
            len(incomplete),
            total,
        )

        return EvidenceChainReport(
            chain_status=status,
            coverage_pct=round(coverage, 4),
            incomplete_node_ids=incomplete,
            review_required=review_required,
        )

    @staticmethod
    def _has_target_type(
        artifact: DoclingGraphArtifact,
        node_id: str,
        ontology_type: str,
    ) -> bool:
        node_by_id = {n.node_id: n for n in artifact.nodes}
        for edge in artifact.edges:
            if edge.from_node != node_id:
                continue
            target = node_by_id.get(edge.to_node)
            if target and target.ontology_type == ontology_type:
                return True
        return False
