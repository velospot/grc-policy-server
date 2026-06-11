from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Optional

from neo4j import GraphDatabase

from grc_policy_server.services.graph.docling_graph_adapter import DoclingGraphArtifact


@dataclass(frozen=True)
class Neo4jSettings:
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = "your_password"
    database: str = "neo4j"


class Neo4jClient:
    def __init__(self, settings: Neo4jSettings):
        self.settings = settings
        self._driver = GraphDatabase.driver(
            settings.uri, auth=(settings.user, settings.password)
        )

    def close(self) -> None:
        self._driver.close()

    def upsert_document_hierarchy(
        self,
        *,
        document_id: str,
        filename: str,
        document_stable_id: str,
        document_family: str,
        content_hash: str,
        nodes: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> None:
        serialized_nodes = [self._serialize_node(node) for node in nodes]
        self._driver.execute_query(
            """
            MERGE (d:Document:HierarchyNode {id: $document_id})
            SET d.document_id = $document_id,
                d.chunk_id = $document_id,
                d.stable_id = $document_stable_id,
                d.document_stable_id = $document_stable_id,
                d.document_family = $document_family,
                d.content_hash = $content_hash,
                d.name = $filename,
                d.node_type = 'document',
                d.metadata_json = $metadata_json,
                d.updated_at = datetime()
            WITH d
            UNWIND $nodes AS node
            MERGE (n:HierarchyNode {id: node.node_id})
            SET n.chunk_id = node.node_id,
                n.stable_id = node.stable_id,
                n.content_hash = node.content_hash,
                n.document_id = node.document_id,
                n.document_stable_id = node.document_stable_id,
                n.node_type = node.node_type,
                n.parent_id = node.parent_id,
                n.title = node.title,
                n.text = coalesce(node.text, ''),
                n.source_text = coalesce(node.text, ''),
                n.section_path = coalesce(node.section_path, 'Unknown Section'),
                n.section_titles = coalesce(node.section_titles, []),
                n.page = coalesce(node.page_number, 0),
                n.page_number = node.page_number,
                n.line_start = null,
                n.line_end = null,
                n.chunk_index = coalesce(node.ordinal, 0),
                n.indexable = coalesce(node.indexable, false),
                n.excluded_from_index = coalesce(node.excluded_from_index, false),
                n.exclusion_reason = node.exclusion_reason,
                n.source = node.source,
                n.lineage = coalesce(node.lineage, []),
                n.lineage_ids = coalesce(node.lineage_ids, []),
                n.metadata_json = node.metadata_json,
                n.updated_at = datetime()
            MERGE (d)-[:HAS_NODE]->(n)
            WITH d, n, node
            MATCH (parent:HierarchyNode {id: coalesce(node.parent_id, $document_id)})
            MERGE (parent)-[rel:HAS_CHILD]->(n)
            SET rel.ordinal = coalesce(node.ordinal, 0)
            """,
            document_id=document_id,
            filename=filename,
            document_stable_id=document_stable_id,
            document_family=document_family,
            content_hash=content_hash,
            metadata_json=json.dumps(metadata, sort_keys=True),
            nodes=serialized_nodes,
            database_=self.settings.database,
        )

    def upsert_docling_graph(self, artifact: DoclingGraphArtifact) -> None:
        """Upsert validated tri-layer Docling graph nodes and relationships.

        The write is idempotent by node_id and relationship endpoints/type.
        Relationship types are generated only from validated internal edge
        labels and sanitized before interpolation into Cypher.
        """
        payloads = [self._serialize_docling_graph_node(node.model_dump(mode="json")) for node in artifact.nodes]
        by_layer: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in payloads:
            by_layer[str(node.get("layer") or "layout")].append(node)

        self._upsert_docling_graph_nodes("MetaGraphNode", by_layer.get("meta", []))
        self._upsert_docling_graph_nodes("LayoutGraphNode", by_layer.get("layout", []))
        self._upsert_docling_graph_nodes("ComplianceNode", by_layer.get("compliance", []))

        edge_payloads = [edge.model_dump(mode="json") for edge in artifact.edges]
        by_rel_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in edge_payloads:
            by_rel_type[_safe_rel_type(str(edge.get("rel_type") or "RELATED_TO"))].append(edge)

        for rel_type, edges in by_rel_type.items():
            self._upsert_docling_graph_edges(rel_type, edges)

    def _upsert_docling_graph_nodes(self, layer_label: str, nodes: list[dict[str, Any]]) -> None:
        if not nodes:
            return
        self._driver.execute_query(
            f"""
            UNWIND $nodes AS node
            MERGE (n:DoclingGraphNode:{layer_label} {{id: node.node_id}})
            SET n.node_id = node.node_id,
                n.stable_id = node.stable_id,
                n.layer = node.layer,
                n.label = node.label,
                n.document_id = node.document_id,
                n.source_node_id = node.source_node_id,
                n.ontology_type = node.ontology_type,
                n.title = node.title,
                n.text = node.text,
                n.language = node.language,
                n.page = node.page,
                n.bbox_refs_json = node.bbox_refs_json,
                n.properties_json = node.properties_json,
                n.updated_at = datetime()
            """,
            nodes=nodes,
            database_=self.settings.database,
        )

    def _upsert_docling_graph_edges(self, rel_type: str, edges: list[dict[str, Any]]) -> None:
        if not edges:
            return
        self._driver.execute_query(
            f"""
            UNWIND $edges AS edge
            MATCH (from_node:DoclingGraphNode {{id: edge.from_node}})
            MATCH (to_node:DoclingGraphNode {{id: edge.to_node}})
            MERGE (from_node)-[rel:{rel_type}]->(to_node)
            SET rel.source = edge.source,
                rel.confidence = edge.confidence,
                rel.ontology_version = edge.ontology_version,
                rel.model_version = edge.model_version,
                rel.review_flag = edge.review_flag,
                rel.properties_json = edge.properties_json,
                rel.updated_at = datetime()
            """,
            edges=[self._serialize_docling_graph_edge(edge) for edge in edges],
            database_=self.settings.database,
        )

    def upsert_document_with_chunks(
        self,
        *,
        document_id: str,
        filename: str,
        chunks: list[dict[str, Any]],
    ) -> None:
        nodes = [
            {
                "node_id": chunk["chunk_id"],
                "stable_id": chunk["chunk_id"],
                "content_hash": "",
                "document_id": document_id,
                "document_stable_id": document_id,
                "node_type": "clause",
                "parent_id": document_id,
                "title": None,
                "text": chunk.get("text") or "",
                "section_path": chunk.get("section_path") or "Unknown Section",
                "section_titles": [chunk.get("section_path") or "Unknown Section"],
                "page_number": chunk.get("page_number"),
                "ordinal": chunk.get("chunk_index") or 0,
                "indexable": True,
                "excluded_from_index": False,
                "exclusion_reason": None,
                "source": "legacy",
                "lineage": [chunk.get("section_path") or "Unknown Section"],
                "lineage_ids": [document_id],
                "metadata": {"docling_path": chunk.get("docling_path")},
            }
            for chunk in chunks
        ]
        self.upsert_document_hierarchy(
            document_id=document_id,
            filename=filename,
            document_stable_id=document_id,
            document_family=filename,
            content_hash="",
            nodes=nodes,
            metadata={"source": "legacy"},
        )

    def delete_document_subgraph(self, document_id: str) -> int:
        recs, _, _ = self._driver.execute_query(
            """
            MATCH (n)
            WHERE n.document_id = $document_id OR n.id = $document_id
            WITH collect(DISTINCT n) AS nodes
            WITH nodes, size(nodes) AS deleted_count
            FOREACH (node IN nodes | DETACH DELETE node)
            RETURN deleted_count
            """,
            document_id=document_id,
            database_=self.settings.database,
        )
        if not recs:
            return 0
        return int(recs[0]["deleted_count"] or 0)

    def resolve_section_path(self, chunk_id: str) -> str:
        recs, _, _ = self._driver.execute_query(
            """
            MATCH (n:HierarchyNode {id: $chunk_id})
            RETURN coalesce(n.section_path, 'Unknown Section') AS path
            LIMIT 1
            """,
            chunk_id=chunk_id,
            database_=self.settings.database,
        )
        return recs[0]["path"] if recs else "Unknown Section"

    def get_chunk_citation(self, *, chunk_id: str) -> Optional[Dict[str, Any]]:
        recs, _, _ = self._driver.execute_query(
            """
            MATCH (n:HierarchyNode {id: $chunk_id})
            RETURN
              coalesce(n.section_path, 'Unknown Section') AS section_path,
              coalesce(n.page, 0) AS page,
              n.line_start AS line_start,
              n.line_end AS line_end,
              coalesce(n.source_text, '') AS source_text
            LIMIT 1
            """,
            chunk_id=chunk_id,
            database_=self.settings.database,
        )

        if not recs:
            return None

        record = recs[0]
        return {
            "section": record["section_path"],
            "page": int(record["page"] or 0),
            "lineStart": record["line_start"],
            "lineEnd": record["line_end"],
            "sourceText": record["source_text"],
        }

    def _serialize_node(self, node: dict[str, Any]) -> dict[str, Any]:
        serialized = dict(node)
        serialized["metadata_json"] = json.dumps(serialized.pop("metadata", {}), sort_keys=True)
        return serialized

    def _serialize_docling_graph_node(self, node: dict[str, Any]) -> dict[str, Any]:
        serialized = dict(node)
        serialized["bbox_refs_json"] = json.dumps(serialized.pop("bbox_refs", []), sort_keys=True)
        serialized["properties_json"] = json.dumps(serialized.pop("properties", {}), sort_keys=True)
        return serialized

    def _serialize_docling_graph_edge(self, edge: dict[str, Any]) -> dict[str, Any]:
        serialized = dict(edge)
        serialized["properties_json"] = json.dumps(serialized.pop("properties", {}), sort_keys=True)
        return serialized


def _safe_rel_type(value: str) -> str:
    rel_type = re.sub(r"[^A-Za-z0-9_]", "_", value.upper()).strip("_")
    return rel_type or "RELATED_TO"
