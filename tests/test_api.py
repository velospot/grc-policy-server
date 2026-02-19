from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from grc_policy_server.api.deps import (
    get_diff_engine,
    get_diff_engine_stream,
    get_document_ingestion_service_factory,
    get_document_repository,
)
from grc_policy_server.main import app
from grc_policy_server.models.domain import DocumentDomain
from grc_policy_server.models.schemas import (
    ActionItem,
    ComparisonResult,
    DocumentReference,
    KeyDifference,
)
from grc_policy_server.services.ingestion.document_ingestion_service import (
    UploadIngestionResult,
)

client = TestClient(app)


def compare_payload() -> dict:
    return {
        "doc1": {
            "id": "policy-v1",
            "name": "Security Policy",
            "version": "1.0",
            "uploadDate": "2026-02-01",
            "size": "2 MB",
            "category": "security",
        },
        "doc2": {
            "id": "policy-v2",
            "name": "Security Policy",
            "version": "2.0",
            "uploadDate": "2026-02-15",
            "size": "2.2 MB",
            "category": "security",
        },
    }


class StubDocumentRepository:
    def list_documents(self) -> list[DocumentDomain]:
        return [
            DocumentDomain(
                id="doc-1",
                name="Vendor Risk Policy",
                version="1.0",
                upload_date=datetime(2026, 2, 1),
                size_bytes=2048,
                category="risk",
                file_path="/tmp/vendor-risk-policy.pdf",
            )
        ]


class StubDiffEngine:
    async def compare(self, doc1, doc2) -> ComparisonResult:
        return ComparisonResult(
            summary=f"Compared {doc1.id} with {doc2.id}",
            keyDifferences=[
                KeyDifference(
                    changeType="MODIFIED",
                    section="Access Control",
                    doc1Content="MFA recommended",
                    doc2Content="MFA required",
                    impact="High",
                    doc1Reference=DocumentReference(
                        section="Access Control",
                        page=2,
                        lineStart=4,
                        lineEnd=7,
                        sourceText="MFA recommended",
                    ),
                    doc2Reference=DocumentReference(
                        section="Access Control",
                        page=2,
                        lineStart=4,
                        lineEnd=7,
                        sourceText="MFA required",
                    ),
                )
            ],
            actionPlan=[
                ActionItem(
                    priority="High",
                    action="Review impacted controls",
                    timeline="30 days",
                    owner="Compliance Team",
                )
            ],
            followUpQuestions=["Which controls need immediate remediation?"],
        )


class StubDiffEngineStream:
    async def compare_stream(self, doc1, doc2):
        yield {"type": "progress", "stage": "load_chunks"}
        yield {
            "type": "diff",
            "item": KeyDifference(
                changeType="ADDED",
                section="Incident Response",
                doc1Content=None,
                doc2Content="24-hour notification added",
                impact="High",
                doc1Reference=None,
                doc2Reference=DocumentReference(
                    section="Incident Response",
                    page=5,
                    lineStart=10,
                    lineEnd=12,
                    sourceText="24-hour notification added",
                ),
            ).model_dump(),
        }
        yield {
            "type": "done",
            "summary": f"Compared {doc1.id} with {doc2.id}",
            "actionPlan": [
                ActionItem(
                    priority="High",
                    action="Update incident response SOP",
                    timeline="30 days",
                    owner="Security Team",
                ).model_dump()
            ],
            "followUpQuestions": ["Is legal review required for the new SLA?"],
        }


class StubDocumentIngestionService:
    async def ingest_upload(self, *, filename: str, content: bytes, content_type: str | None):
        assert filename == "policy.pdf"
        assert content == b"policy content"
        assert content_type == "application/pdf"
        return UploadIngestionResult(document_id="doc-upload-1", chunks_stored=3)


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_swagger_ui_is_available():
    response = client.get("/docs")
    assert response.status_code == 200
    assert "Swagger UI" in response.text


def test_openapi_includes_core_routes():
    response = client.get("/openapi.json")
    assert response.status_code == 200

    paths = response.json()["paths"]
    assert "/health" in paths
    assert "/documents" in paths
    assert "/documents/upload" in paths
    assert "/compare" in paths
    assert "/compare/with-summary" in paths


def test_list_documents():
    app.dependency_overrides[get_document_repository] = lambda: StubDocumentRepository()
    response = client.get("/documents")
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "doc-1",
            "name": "Vendor Risk Policy",
            "version": "1.0",
            "uploadDate": "2026-02-01",
            "size": "2 KB",
            "category": "risk",
        }
    ]


def test_upload_document():
    app.dependency_overrides[get_document_ingestion_service_factory] = (
        lambda: (lambda: StubDocumentIngestionService())
    )
    response = client.post(
        "/documents/upload",
        files={"file": ("policy.pdf", b"policy content", "application/pdf")},
    )
    assert response.status_code == 201
    assert response.json() == {
        "filename": "policy.pdf",
        "contentType": "application/pdf",
        "accepted": True,
        "documentId": "doc-upload-1",
        "chunksStored": 3,
    }


def test_upload_document_rejects_empty_file():
    response = client.post(
        "/documents/upload",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded file is empty"


def test_compare_documents():
    app.dependency_overrides[get_diff_engine] = lambda: StubDiffEngine()
    response = client.post("/compare", json=compare_payload())
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"] == "Compared policy-v1 with policy-v2"
    assert payload["keyDifferences"][0]["changeType"] == "MODIFIED"


def test_compare_with_summary():
    app.dependency_overrides[get_diff_engine_stream] = lambda: StubDiffEngineStream()
    response = client.post("/compare/with-summary", json=compare_payload())
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"] == "Compared policy-v1 with policy-v2"
    assert payload["keyDifferences"][0]["changeType"] == "ADDED"
