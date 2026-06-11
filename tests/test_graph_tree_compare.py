from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from grc_policy_server.api.deps import get_graph_tree_comparison_orchestrator
from grc_policy_server.core.config import settings
from grc_policy_server.main import app
from grc_policy_server.models.schemas import ComparisonResult
from grc_policy_server.services.agents.graph_explanation_agent import GraphExplanationAgent
from grc_policy_server.services.comparison.graph_tree_compare import (
    GraphArtifactStore,
    GraphTreeComparisonOrchestrator,
)
from grc_policy_server.services.graph.docling_graph_adapter import (
    DoclingGraphArtifact,
    DoclingGraphEdge,
    DoclingGraphNode,
)
from grc_policy_server.services.llm.noop_llm import NoOpLLM


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.api_bearer_token}"}


@pytest.mark.anyio
async def test_graph_tree_compare_detects_measurement_value_change(tmp_path) -> None:
    _write_graph(tmp_path, "doc-a", value="30")
    _write_graph(tmp_path, "doc-b", value="40")

    orchestrator = GraphTreeComparisonOrchestrator(
        artifact_store=GraphArtifactStore(upload_root=tmp_path),
    )

    result = await orchestrator.compare(doc1_id="doc-a", doc2_id="doc-b")

    assert result.comparisonMode == "document_graph_tree"
    assert result.summary.totalChanges == 1
    assert result.summary.highSeverity == 1
    assert result.summary.requiresHumanReview is True
    change = result.changes[0]
    assert change.changeType == "MODIFIED"
    assert change.ontologyType == "Measurement"
    assert change.propertyChanges[0].path == "value"
    assert change.propertyChanges[0].oldValue == "30"
    assert change.propertyChanges[0].newValue == "40"


@pytest.mark.anyio
async def test_graph_tree_compare_enriches_current_response_with_llm_explanation(tmp_path) -> None:
    _write_graph(tmp_path, "doc-a", value="30")
    _write_graph(tmp_path, "doc-b", value="40")

    orchestrator = GraphTreeComparisonOrchestrator(
        artifact_store=GraphArtifactStore(upload_root=tmp_path),
        explanation_agent=GraphExplanationAgent(llm=_StubExplanationLLM()),
    )

    result = await orchestrator.compare_as_current_response(
        doc1_id="doc-a",
        doc2_id="doc-b",
        testing_department="EMC",
    )

    diff = result.keyDifferences[0]
    assert diff.markdownDiffSummary == "Limit increased from 30 V/m to 40 V/m."
    assert diff.complianceExplanation == "Limit increased from 30 V/m to 40 V/m."


def test_graph_compare_rest_endpoint_uses_separate_route(monkeypatch) -> None:
    # POST /graph-compare now queues via Celery and returns a job ID.
    # Stub send_task so no real Celery broker is required.
    class _FakeResult:
        id = "fake-graph-job-id"

    import grc_policy_server.api.routes.graph_compare as _route_mod
    monkeypatch.setattr(
        _route_mod,
        "_enqueue_graph_compare",
        lambda task_payload, dispatcher: "fake-graph-job-id",
    )

    client = TestClient(app)
    _doc = lambda doc_id: {"id": doc_id, "name": doc_id, "version": "1.0", "uploadDate": "2026-01-01", "size": "1MB", "category": "standard"}
    response = client.post(
        "/graph-compare",
        json={"doc1": _doc("doc-a"), "doc2": _doc("doc-b")},
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["jobId"] == "fake-graph-job-id"
    assert body["status"] == "queued"
    assert body["cacheHit"] is False


def test_graph_compare_stream_endpoint_emits_sse_events() -> None:
    class StubOrchestrator:
        async def to_current_response(self, graph_result, *, testing_department: str = ""):
            return ComparisonResult(
                summary="No compliance graph differences detected between doc-a and doc-b.",
                keyDifferences=[],
                actionPlan=[],
                followUpQuestions=[],
                comparisonMode="auditor_grade",
                requireHumanReview=False,
                warnings=[],
            )

        async def compare_events(self, **kwargs):
            yield {"type": "payload", "doc1_id": kwargs["doc1_id"], "doc2_id": kwargs["doc2_id"]}
            yield {"type": "progress", "stage": "align_graph_trees"}
            yield {
                "type": "done",
                "result": {
                    "comparisonId": "cmp-1",
                    "doc1Id": kwargs["doc1_id"],
                    "doc2Id": kwargs["doc2_id"],
                    "comparisonMode": "document_graph_tree",
                    "summary": {
                        "totalChanges": 0,
                        "added": 0,
                        "removed": 0,
                        "modified": 0,
                        "unchanged": 0,
                        "highSeverity": 0,
                        "mediumSeverity": 0,
                        "lowSeverity": 0,
                        "requiresHumanReview": False,
                    },
                    "changes": [],
                    "warnings": [],
                },
            }

    app.dependency_overrides[get_graph_tree_comparison_orchestrator] = lambda: StubOrchestrator()
    try:
        client = TestClient(app)
        _doc = lambda doc_id: {"id": doc_id, "name": doc_id, "version": "1.0", "uploadDate": "2026-01-01", "size": "1MB", "category": "standard"}
        with client.stream(
            "POST",
            "/graph-compare/stream",
            json={"doc1": _doc("doc-a"), "doc2": _doc("doc-b")},
            headers=_auth_headers(),
        ) as response:
            body = response.read().decode("utf-8")
        assert response.status_code == 200
        assert "data:" in body
        assert '"type": "payload"' in body
        assert '"type": "progress"' in body
        assert '"type": "done"' in body
        assert '"keyDifferences"' in body
    finally:
        app.dependency_overrides.pop(get_graph_tree_comparison_orchestrator, None)


def _write_graph(tmp_path, document_id: str, *, value: str) -> None:
    target = tmp_path / document_id
    target.mkdir()
    artifact = DoclingGraphArtifact(
        document_id=document_id,
        document_stable_id=f"stable-{document_id}",
        nodes=[
            DoclingGraphNode(
                node_id=f"meta:document:{document_id}",
                stable_id=f"stable-{document_id}",
                layer="meta",
                label="Document",
                document_id=document_id,
                title=f"{document_id}.pdf",
            ),
            DoclingGraphNode(
                node_id=f"layout:table:{document_id}",
                stable_id="stable-layout-table",
                layer="layout",
                label="Table",
                document_id=document_id,
                source_node_id="table-1",
                page=1,
                properties={"section_path": "6 EMC"},
            ),
            DoclingGraphNode(
                node_id=f"compliance:table:{document_id}",
                stable_id="stable-compliance-table",
                layer="compliance",
                label="Measurement",
                document_id=document_id,
                source_node_id="table-1",
                ontology_type="Measurement",
                title="Radiated immunity",
                properties={"section_path": "6 EMC", "clause": "6"},
            ),
            DoclingGraphNode(
                node_id=f"compliance:fact:{document_id}",
                stable_id="stable-fact",
                layer="compliance",
                label="Measurement",
                document_id=document_id,
                source_node_id="table-1",
                ontology_type="Measurement",
                title="field_strength",
                text=f"{value} V/m",
                properties={
                    "section_path": "6 EMC",
                    "fact_type": "field_strength",
                    "name": "field_strength",
                    "value": value,
                    "unit": "V/m",
                    "row": 1,
                    "col": 1,
                    "column_header": "Prüfpegel",
                },
            ),
        ],
        edges=[
            DoclingGraphEdge(
                from_node=f"compliance:table:{document_id}",
                to_node=f"layout:table:{document_id}",
                rel_type="SOURCED_FROM",
            ),
            DoclingGraphEdge(
                from_node=f"compliance:table:{document_id}",
                to_node=f"compliance:fact:{document_id}",
                rel_type="HAS_FACT",
            ),
        ],
    )
    (target / "docling_graph.json").write_text(
        json.dumps(artifact.model_dump(mode="json")),
        encoding="utf-8",
    )


class _StubExplanationLLM(NoOpLLM):
    async def generate_markdown_diff_summary(
        self,
        *,
        node_type: str,
        change_type: str,
        doc1_source_text: str | None,
        doc2_source_text: str | None,
        doc1_table_content: str | None = None,
        doc2_table_content: str | None = None,
        language: str = "",
        testing_department: str | None = None,
    ) -> str:
        return "Limit increased from 30 V/m to 40 V/m."
