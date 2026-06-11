from __future__ import annotations

import json

from grc_policy_server.services.graph.docling_graph_adapter import DoclingGraphAdapter
from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient, Neo4jSettings


def test_docling_graph_adapter_builds_multilingual_compliance_facts() -> None:
    artifact = DoclingGraphAdapter().build_artifact(
        document_id="doc-1",
        filename="emv-report.pdf",
        document_stable_id="stable-doc",
        document_family="emv-report",
        content_hash="hash-1",
        metadata={},
        nodes=[
            {
                "node_id": "section-1",
                "stable_id": "stable-section",
                "node_type": "section",
                "parent_id": "doc-1",
                "title": "6 Strahlungsimmunität",
                "text": "Das Gerät muss bestehen.",
                "section_path": "6 Strahlungsimmunität",
                "section_titles": ["6 Strahlungsimmunität"],
                "page_number": 1,
                "ordinal": 1,
                "excluded_from_index": False,
                "metadata": {"detected_language": "de"},
            },
            {
                "node_id": "table-1",
                "stable_id": "stable-table",
                "node_type": "table",
                "parent_id": "section-1",
                "title": "Strahlungsimmunität",
                "text": "Frequenzbereich | Prüfpegel\n80 MHz - 1000 MHz | 30 V/m",
                "section_path": "6 Strahlungsimmunität",
                "section_titles": ["6 Strahlungsimmunität"],
                "page_number": 2,
                "ordinal": 2,
                "excluded_from_index": False,
                "metadata": {
                    "detected_language": "de",
                    "table_headers": ["Frequenzbereich", "Prüfpegel"],
                    "normalized_caption": "Strahlungsimmunität",
                    "table_structure": {
                        "num_rows": 2,
                        "num_cols": 2,
                        "headers": ["Frequenzbereich", "Prüfpegel"],
                        "cells": [
                            {"row": 0, "col": 0, "text": "Frequenzbereich", "is_header": True},
                            {"row": 0, "col": 1, "text": "Prüfpegel", "is_header": True},
                            {"row": 1, "col": 0, "text": "80 MHz - 1000 MHz", "is_header": False},
                            {"row": 1, "col": 1, "text": "30 V/m", "is_header": False},
                        ],
                    },
                },
            },
            {
                "node_id": "toc-1",
                "stable_id": "stable-toc",
                "node_type": "clause",
                "parent_id": "doc-1",
                "title": "Contents",
                "text": "1 Introduction .... 1",
                "section_path": "Contents",
                "section_titles": ["Contents"],
                "page_number": 1,
                "ordinal": 3,
                "excluded_from_index": True,
                "exclusion_reason": "table_of_contents",
                "metadata": {},
            },
        ],
    )

    compliance_nodes = [node for node in artifact.nodes if node.layer == "compliance"]
    assert any(node.ontology_type == "Measurement" for node in compliance_nodes)
    assert any(
        node.properties.get("fact_type") == "frequency_range"
        and node.properties.get("unit") == "Hz"
        for node in compliance_nodes
    )
    assert any(
        node.properties.get("fact_type") == "field_strength"
        and node.properties.get("unit") == "V/m"
        for node in compliance_nodes
    )
    assert not any(node.source_node_id == "toc-1" for node in compliance_nodes)
    assert artifact.ignored_nodes[0]["reason"] == "table_of_contents"
    assert any(edge.rel_type == "SOURCED_FROM" for edge in artifact.edges)


def test_neo4j_client_upserts_docling_graph_layers_and_relationships() -> None:
    artifact = DoclingGraphAdapter().build_artifact(
        document_id="doc-1",
        filename="safety.pdf",
        document_stable_id="stable-doc",
        document_family="safety",
        content_hash="hash-1",
        metadata={},
        nodes=[
            {
                "node_id": "clause-1",
                "stable_id": "stable-clause",
                "node_type": "clause",
                "parent_id": "doc-1",
                "title": "Safety requirement",
                "text": "The enclosure shall provide IP67 protection.",
                "section_path": "ISO 45001 / Safety",
                "section_titles": ["ISO 45001", "Safety"],
                "page_number": 3,
                "ordinal": 1,
                "excluded_from_index": False,
                "metadata": {"detected_language": "en"},
            }
        ],
    )
    fake_driver = _FakeDriver()
    client = Neo4jClient.__new__(Neo4jClient)
    client.settings = Neo4jSettings(database="neo4j")
    client._driver = fake_driver

    client.upsert_docling_graph(artifact)

    queries = "\n".join(call["query"] for call in fake_driver.calls)
    assert "MetaGraphNode" in queries
    assert "LayoutGraphNode" in queries
    assert "ComplianceNode" in queries
    assert "SOURCED_FROM" in queries
    compliance_call = next(call for call in fake_driver.calls if "ComplianceNode" in call["query"])
    node_payload = compliance_call["kwargs"]["nodes"][0]
    assert node_payload["layer"] == "compliance"
    assert json.loads(node_payload["properties_json"])["classification"]["method"] == "deterministic_keyword"


class _FakeDriver:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute_query(self, query: str, **kwargs):
        self.calls.append({"query": query, "kwargs": kwargs})
        return [], None, None

