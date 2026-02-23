from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from grc_policy_server.api.deps import (
    get_diff_engine,
    get_diff_engine_stream,
    get_document_ingestion_service_factory,
    get_document_repository,
    get_weaviate_client,
)
from grc_policy_server.core.config import settings
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


def auth_headers(token: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token or settings.api_bearer_token}"}


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
        assert content_type == "application/pdf"
        if filename == "policy.pdf":
            assert content == b"policy content"
            return UploadIngestionResult(document_id="doc-upload-1", chunks_stored=3)
        if filename == "policy-2.pdf":
            assert content == b"policy second content"
            return UploadIngestionResult(document_id="doc-upload-2", chunks_stored=5)
        raise AssertionError(f"Unexpected filename: {filename}")


class StubDeleteDocumentRepository:
    def __init__(self, *, delete_results: dict[str, bool] | None = None):
        self.delete_results = delete_results or {}
        self.deleted_document_ids: list[str] = []

    def delete_document(self, document_id: str) -> bool:
        self.deleted_document_ids.append(document_id)
        return self.delete_results.get(document_id, False)


class StubWeaviateDeleteClient:
    def __init__(
        self,
        *,
        deleted_chunks: dict[str, int] | None = None,
        failing_document_ids: set[str] | None = None,
    ):
        self.deleted_chunks = deleted_chunks or {}
        self.failing_document_ids = failing_document_ids or set()
        self.deleted_document_ids: list[str] = []

    def delete_chunks_by_document(self, document_id: str) -> int:
        self.deleted_document_ids.append(document_id)
        if document_id in self.failing_document_ids:
            raise RuntimeError("weaviate deletion failure")
        return self.deleted_chunks.get(document_id, 0)


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

    schema = response.json()
    paths = schema["paths"]
    assert "/health" in paths
    assert "/documents" in paths
    assert "/documents/delete" in paths
    assert "/documents/upload" in paths
    assert "/compare" in paths
    assert "/compare/with-summary" in paths

    security_schemes = schema["components"]["securitySchemes"]
    assert any(
        scheme.get("type") == "http" and scheme.get("scheme") == "bearer"
        for scheme in security_schemes.values()
    )
    assert paths["/documents"]["get"]["security"]


def test_cors_preflight_documents():
    response = client.options(
        "/documents",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_protected_route_requires_bearer_token():
    response = client.get("/documents")
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing bearer token"}


def test_protected_route_rejects_invalid_bearer_token():
    response = client.get(
        "/documents",
        headers=auth_headers(token="invalid-token"),
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid bearer token"}


def test_list_documents():
    app.dependency_overrides[get_document_repository] = lambda: StubDocumentRepository()
    response = client.get("/documents", headers=auth_headers())
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
        headers=auth_headers(),
    )
    assert response.status_code == 201
    assert response.json() == {
        "acceptedCount": 1,
        "rejectedCount": 0,
        "results": [
            {
                "filename": "policy.pdf",
                "contentType": "application/pdf",
                "accepted": True,
                "documentId": "doc-upload-1",
                "chunksStored": 3,
                "error": None,
            }
        ],
    }


def test_upload_multiple_documents():
    app.dependency_overrides[get_document_ingestion_service_factory] = (
        lambda: (lambda: StubDocumentIngestionService())
    )
    response = client.post(
        "/documents/upload",
        files=[
            ("file", ("policy.pdf", b"policy content", "application/pdf")),
            ("file", ("policy-2.pdf", b"policy second content", "application/pdf")),
        ],
        headers=auth_headers(),
    )
    assert response.status_code == 201
    assert response.json() == {
        "acceptedCount": 2,
        "rejectedCount": 0,
        "results": [
            {
                "filename": "policy.pdf",
                "contentType": "application/pdf",
                "accepted": True,
                "documentId": "doc-upload-1",
                "chunksStored": 3,
                "error": None,
            },
            {
                "filename": "policy-2.pdf",
                "contentType": "application/pdf",
                "accepted": True,
                "documentId": "doc-upload-2",
                "chunksStored": 5,
                "error": None,
            },
        ],
    }


def test_upload_document_rejects_empty_file_in_results():
    response = client.post(
        "/documents/upload",
        files={"file": ("empty.pdf", b"", "application/pdf")},
        headers=auth_headers(),
    )
    assert response.status_code == 201
    assert response.json() == {
        "acceptedCount": 0,
        "rejectedCount": 1,
        "results": [
            {
                "filename": "empty.pdf",
                "contentType": "application/pdf",
                "accepted": False,
                "documentId": None,
                "chunksStored": None,
                "error": "Uploaded file is empty",
            }
        ],
    }


def test_delete_documents():
    repository = StubDeleteDocumentRepository(
        delete_results={
            "doc-local-only": True,
            "doc-local-and-vectors": True,
            "doc-vectors-only": False,
            "doc-missing": False,
        }
    )
    weaviate = StubWeaviateDeleteClient(
        deleted_chunks={
            "doc-local-only": 0,
            "doc-local-and-vectors": 4,
            "doc-vectors-only": 2,
            "doc-missing": 0,
        }
    )
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_weaviate_client] = lambda: weaviate

    response = client.post(
        "/documents/delete",
        json={
            "documentIds": [
                "doc-local-only",
                "doc-local-and-vectors",
                "doc-vectors-only",
                "doc-missing",
            ]
        },
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "deletedCount": 3,
        "failedCount": 1,
        "results": [
            {
                "documentId": "doc-local-only",
                "deleted": True,
                "deletedChunks": 0,
                "error": None,
            },
            {
                "documentId": "doc-local-and-vectors",
                "deleted": True,
                "deletedChunks": 4,
                "error": None,
            },
            {
                "documentId": "doc-vectors-only",
                "deleted": True,
                "deletedChunks": 2,
                "error": None,
            },
            {
                "documentId": "doc-missing",
                "deleted": False,
                "deletedChunks": 0,
                "error": "Document not found",
            },
        ],
    }


def test_delete_documents_rejects_duplicate_and_blank_ids():
    repository = StubDeleteDocumentRepository(delete_results={"doc-1": True})
    weaviate = StubWeaviateDeleteClient(deleted_chunks={"doc-1": 3})
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_weaviate_client] = lambda: weaviate

    response = client.post(
        "/documents/delete",
        json={"documentIds": ["doc-1", "", "doc-1"]},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "deletedCount": 1,
        "failedCount": 2,
        "results": [
            {
                "documentId": "doc-1",
                "deleted": True,
                "deletedChunks": 3,
                "error": None,
            },
            {
                "documentId": "",
                "deleted": False,
                "deletedChunks": None,
                "error": "Document id must not be empty",
            },
            {
                "documentId": "doc-1",
                "deleted": False,
                "deletedChunks": None,
                "error": "Duplicate document id in request",
            },
        ],
    }


def test_delete_documents_returns_error_on_weaviate_failure():
    repository = StubDeleteDocumentRepository(delete_results={"doc-1": True})
    weaviate = StubWeaviateDeleteClient(failing_document_ids={"doc-1"})
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_weaviate_client] = lambda: weaviate

    response = client.post(
        "/documents/delete",
        json={"documentIds": ["doc-1"]},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "deletedCount": 0,
        "failedCount": 1,
        "results": [
            {
                "documentId": "doc-1",
                "deleted": False,
                "deletedChunks": None,
                "error": "Failed to delete document records from Weaviate",
            }
        ],
    }
    assert repository.deleted_document_ids == []


def test_compare_documents():
    app.dependency_overrides[get_diff_engine] = lambda: StubDiffEngine()
    response = client.post("/compare", json=compare_payload(), headers=auth_headers())
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"] == "Compared policy-v1 with policy-v2"
    assert payload["keyDifferences"][0]["changeType"] == "MODIFIED"


def test_compare_with_summary():
    app.dependency_overrides[get_diff_engine_stream] = lambda: StubDiffEngineStream()
    response = client.post(
        "/compare/with-summary",
        json=compare_payload(),
        headers=auth_headers(),
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"] == "Compared policy-v1 with policy-v2"
    assert payload["keyDifferences"][0]["changeType"] == "ADDED"
