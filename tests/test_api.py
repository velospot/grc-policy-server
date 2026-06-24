import base64
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import grc_policy_server.api.deps as api_deps
from grc_policy_server.api.deps import (
    get_compare_v2_dispatcher,
    get_compare_v5_dispatcher,
    get_comparison_cache_store,
    get_diff_engine,
    get_diff_engine_stream,
    get_document_ingestion_service_factory,
    get_document_repository,
    get_neo4j_client,
    get_qdrant_client,
    get_upload_v2_dispatcher,
)
from grc_policy_server.core.config import settings
from grc_policy_server.main import app
from grc_policy_server.models.domain import DocumentDomain
from grc_policy_server.models.schemas import (
    ActionItem,
    ComparisonResult,
    CompareRequest,
    DocumentReference,
    KeyDifference,
)
from grc_policy_server.repositories.documents import DocumentRepository
from grc_policy_server.services.comparison.compare_v2_dispatcher import (
    CeleryNotAvailableError,
)
from grc_policy_server.services.comparison.compare_v2_models import CompareTaskPayload
from grc_policy_server.services.ingestion.document_ingestion_service import (
    UploadIngestionResult,
)
from grc_policy_server.services.ingestion.upload_v2_dispatcher import (
    CeleryWorkerUnavailableError,
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


class StubCompareDocumentRepository:
    def __init__(self, missing: set[str] | None = None) -> None:
        self.missing = missing or set()

    def get_document(self, document_id: str) -> DocumentDomain | None:
        if document_id in self.missing:
            return None
        return DocumentDomain(
            id=document_id,
            name=f"{document_id}.pdf",
            version="1.0",
            upload_date=datetime(2026, 2, 1),
            size_bytes=2048,
            category="standard",
            file_path=f"/tmp/{document_id}.pdf",
        )


class StubCanonicalStore:
    def __init__(self, nodes_by_document: dict[str, list[dict]]) -> None:
        self.nodes_by_document = nodes_by_document

    def load_comparison_nodes(self, document_id: str) -> list[dict]:
        return self.nodes_by_document.get(document_id, [])


def test_qdrant_dependency_does_not_swallow_endpoint_exceptions(monkeypatch):
    class StubRemote:
        def get_collections(self):
            return []

    class StubQdrantClient:
        def __init__(self) -> None:
            self._client = StubRemote()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(api_deps, "QdrantVectorClient", StubQdrantClient)

    generator = api_deps.get_qdrant_client()
    client_instance = next(generator)
    assert isinstance(client_instance, StubQdrantClient)

    with pytest.raises(RuntimeError, match="endpoint failed"):
        generator.throw(RuntimeError("endpoint failed"))
    assert client_instance.closed is True


class StubDiffEngine:
    def __init__(self, canonical_store=None) -> None:
        self.calls = []
        self.canonical_store = canonical_store

    async def compare(
        self,
        doc1,
        doc2,
        force_re_extract: bool = False,
        audit_mode: bool = False,
        save_to_db: bool = False,
        testing_department: str = "",
    ) -> ComparisonResult:
        self.calls.append(
            {
                "doc1_id": doc1.id,
                "doc2_id": doc2.id,
                "force_re_extract": force_re_extract,
                "audit_mode": audit_mode,
                "save_to_db": save_to_db,
                "testing_department": testing_department,
            }
        )
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
    def __init__(self) -> None:
        self.v4_calls = []

    async def compare_stream(self, doc1, doc2, force_re_extract: bool = False):
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

    async def compare_stream_v4(
        self,
        doc1,
        doc2,
        force_re_extract: bool = False,
        testing_department: str | None = None,
    ):
        self.v4_calls.append(
            {
                "doc1_id": doc1.id,
                "doc2_id": doc2.id,
                "force_re_extract": force_re_extract,
                "testing_department": testing_department,
            }
        )
        yield {
            "type": "payload",
            "doc1_id": doc1.id,
            "doc2_id": doc2.id,
            "testing_department": testing_department,
        }
        yield {
            "type": "done",
            "total_diffs": 0,
            "accuracy_metrics": None,
        }


class StubCompareV2Dispatcher:
    def __init__(
        self,
        *,
        enqueue_job_id: str = "compare-job-1",
        status_payload: dict | None = None,
        enqueue_error: Exception | None = None,
        status_error: Exception | None = None,
    ):
        self.enqueue_job_id = enqueue_job_id
        self.status_payload = status_payload or {
            "jobId": enqueue_job_id,
            "status": "queued",
            "done": False,
            "result": None,
            "error": None,
            "cacheHit": False,
        }
        self.enqueue_error = enqueue_error
        self.status_error = status_error
        self.enqueue_calls = []
        self.status_calls = []

    def enqueue_compare(self, payload):
        self.enqueue_calls.append(payload)
        if self.enqueue_error is not None:
            raise self.enqueue_error
        return self.enqueue_job_id

    def get_compare_status(self, *, job_id: str):
        self.status_calls.append(job_id)
        if self.status_error is not None:
            raise self.status_error
        return self.status_payload


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


class StubUploadV2Dispatcher:
    def __init__(
        self,
        *,
        job_id: str = "job-1",
        status_payload: dict | None = None,
        enqueue_error: Exception | None = None,
        status_error: Exception | None = None,
    ):
        self.job_id = job_id
        self.status_payload = status_payload or {
            "jobId": job_id,
            "status": "queued",
            "done": False,
            "result": None,
            "error": None,
        }
        self.enqueue_error = enqueue_error
        self.status_error = status_error
        self.enqueue_calls = []
        self.status_calls = []

    def enqueue_uploads(self, payload_files):
        self.enqueue_calls.append(payload_files)
        if self.enqueue_error is not None:
            raise self.enqueue_error
        return self.job_id

    def get_upload_status(self, job_id: str):
        self.status_calls.append(job_id)
        if self.status_error is not None:
            raise self.status_error
        return self.status_payload


class StubDeleteDocumentRepository:
    def __init__(self, *, delete_results: dict[str, bool] | None = None):
        self.delete_results = delete_results or {}
        self.deleted_document_ids: list[str] = []

    def delete_document(self, document_id: str) -> bool:
        self.deleted_document_ids.append(document_id)
        return self.delete_results.get(document_id, False)


class StubQdrantDeleteClient:
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
            raise RuntimeError("qdrant deletion failure")
        return self.deleted_chunks.get(document_id, 0)


class StubNeo4jDeleteClient:
    def __init__(
        self,
        *,
        deleted_nodes: dict[str, int] | None = None,
        failing_document_ids: set[str] | None = None,
    ):
        self.deleted_nodes = deleted_nodes or {}
        self.failing_document_ids = failing_document_ids or set()
        self.deleted_document_ids: list[str] = []

    def delete_document_subgraph(self, document_id: str) -> int:
        self.deleted_document_ids.append(document_id)
        if document_id in self.failing_document_ids:
            raise RuntimeError("neo4j deletion failure")
        return self.deleted_nodes.get(document_id, 0)


class StubQdrantHybridSearchClient:
    def __init__(
        self,
        *,
        results_by_document: dict[str, list[dict]] | None = None,
        should_fail: bool = False,
    ):
        self.results_by_document = results_by_document or {}
        self.should_fail = should_fail
        self.calls: list[dict[str, str | int]] = []

    def hybrid_search_in_document(
        self,
        *,
        query_string: str,
        target_document_id: str,
        limit: int = 3,
    ) -> list[dict]:
        self.calls.append(
            {
                "query_string": query_string,
                "target_document_id": target_document_id,
                "limit": limit,
            }
        )
        if self.should_fail:
            raise RuntimeError("hybrid search failure")
        return self.results_by_document.get(target_document_id, [])


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_swagger_ui_is_available():
    response = client.get("/docs")
    assert response.status_code == 200
    assert "Swagger UI" in response.text


def test_health_is_available():
    response = client.get("/health")
    assert response.status_code == 200


def test_write_requests_are_not_globally_blocked():
    app.dependency_overrides[get_diff_engine] = lambda: StubDiffEngine()
    response = client.post("/compare", json=compare_payload(), headers=auth_headers())
    assert response.status_code == 200


def test_openapi_includes_core_routes():
    response = client.get("/openapi.json")
    assert response.status_code == 200

    schema = response.json()
    paths = schema["paths"]
    assert "/health" in paths
    assert "/documents" in paths
    assert "/documents/{document_id}/download" in paths
    assert "/documents/delete" in paths
    assert "/documents/search/hybrid" in paths
    assert "/documents/upload" in paths
    assert "/documents/upload/v2" in paths
    assert "/documents/upload/v2/{job_id}" in paths
    assert "/compare" in paths
    assert "/compare/with-summary" in paths
    assert "/v2/compare" in paths
    assert "/v2/compare/response" in paths
    assert "/v2/compare/response/{job_id}" in paths
    assert "/v5/compare" in paths
    assert "/v5/compare/stream" in paths
    assert (
        paths["/v5/compare"]["post"]["requestBody"]["content"]["application/json"][
            "schema"
        ]
        == paths["/v2/compare"]["post"]["requestBody"]["content"]["application/json"][
            "schema"
        ]
    )
    assert (
        paths["/v5/compare"]["post"]["responses"]["202"]["content"][
            "application/json"
        ]["schema"]
        == paths["/v2/compare"]["post"]["responses"]["202"]["content"][
            "application/json"
        ]["schema"]
    )

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


def test_download_document_default_pdf(tmp_path):
    doc_dir = tmp_path / "doc-1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "policy.pdf").write_bytes(b"%PDF-1.7 default")
    (doc_dir / "metadata.json").write_text(
        '{"id":"doc-1","stored_filename":"policy.pdf"}',
        encoding="utf-8",
    )

    app.dependency_overrides[get_document_repository] = lambda: DocumentRepository(
        upload_root=tmp_path
    )
    response = client.get("/documents/doc-1/download", headers=auth_headers())

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment; filename=\"policy.pdf\"" in response.headers["content-disposition"]
    assert response.content == b"%PDF-1.7 default"


def test_download_document_specific_pdf(tmp_path):
    doc_dir = tmp_path / "doc-1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "policy-v1.pdf").write_bytes(b"%PDF-1.7 v1")
    (doc_dir / "policy-v2.pdf").write_bytes(b"%PDF-1.7 v2")
    (doc_dir / "metadata.json").write_text(
        '{"id":"doc-1","stored_filename":"policy-v1.pdf"}',
        encoding="utf-8",
    )

    app.dependency_overrides[get_document_repository] = lambda: DocumentRepository(
        upload_root=tmp_path
    )
    response = client.get(
        "/documents/doc-1/download?filename=policy-v2.pdf",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment; filename=\"policy-v2.pdf\"" in response.headers["content-disposition"]
    assert response.content == b"%PDF-1.7 v2"


def test_download_document_rejects_non_pdf_filename(tmp_path):
    doc_dir = tmp_path / "doc-1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "policy.pdf").write_bytes(b"%PDF-1.7")
    (doc_dir / "metadata.json").write_text(
        '{"id":"doc-1","stored_filename":"policy.pdf"}',
        encoding="utf-8",
    )

    app.dependency_overrides[get_document_repository] = lambda: DocumentRepository(
        upload_root=tmp_path
    )
    response = client.get(
        "/documents/doc-1/download?filename=policy.txt",
        headers=auth_headers(),
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Only PDF files can be downloaded from this endpoint"
    }


def test_download_document_returns_404_for_missing_document(tmp_path):
    app.dependency_overrides[get_document_repository] = lambda: DocumentRepository(
        upload_root=tmp_path
    )
    response = client.get("/documents/missing/download", headers=auth_headers())

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


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


def test_upload_document_v2():
    dispatcher = StubUploadV2Dispatcher(job_id="upload-job-123")
    app.dependency_overrides[get_upload_v2_dispatcher] = lambda: dispatcher

    response = client.post(
        "/documents/upload/v2",
        files={"file": ("policy.pdf", b"policy content", "application/pdf")},
        headers=auth_headers(),
    )
    assert response.status_code == 202
    assert response.json() == {
        "jobId": "upload-job-123",
        "status": "queued",
    }
    assert len(dispatcher.enqueue_calls) == 1
    payload_file = dispatcher.enqueue_calls[0][0]
    assert payload_file.filename == "policy.pdf"
    assert payload_file.content_type == "application/pdf"
    assert (
        base64.b64decode(payload_file.content_base64.encode("ascii")) == b"policy content"
    )


def test_upload_document_v2_returns_503_when_worker_unavailable():
    dispatcher = StubUploadV2Dispatcher(
        enqueue_error=CeleryWorkerUnavailableError(
            "No active Celery workers detected for upload v2 endpoint"
        )
    )
    app.dependency_overrides[get_upload_v2_dispatcher] = lambda: dispatcher

    response = client.post(
        "/documents/upload/v2",
        files={"file": ("policy.pdf", b"policy content", "application/pdf")},
        headers=auth_headers(),
    )
    assert response.status_code == 503
    assert response.json() == {
        "detail": "No active Celery workers detected for upload v2 endpoint"
    }


def test_upload_document_v2_status_finished():
    dispatcher = StubUploadV2Dispatcher(
        job_id="upload-job-456",
        status_payload={
            "jobId": "upload-job-456",
            "status": "finished",
            "done": True,
            "result": {
                "acceptedCount": 1,
                "rejectedCount": 0,
                "results": [
                    {
                        "filename": "policy.pdf",
                        "contentType": "application/pdf",
                        "accepted": True,
                        "documentId": "doc-upload-v2-1",
                        "chunksStored": 3,
                        "error": None,
                    }
                ],
            },
            "error": None,
        },
    )
    app.dependency_overrides[get_upload_v2_dispatcher] = lambda: dispatcher

    response = client.get("/documents/upload/v2/upload-job-456", headers=auth_headers())
    assert response.status_code == 200
    assert response.json() == {
        "jobId": "upload-job-456",
        "status": "finished",
        "done": True,
        "result": {
            "acceptedCount": 1,
            "rejectedCount": 0,
            "results": [
                {
                    "filename": "policy.pdf",
                    "contentType": "application/pdf",
                    "accepted": True,
                    "documentId": "doc-upload-v2-1",
                    "chunksStored": 3,
                    "error": None,
                }
            ],
        },
        "error": None,
    }
    assert dispatcher.status_calls == ["upload-job-456"]


def test_delete_documents():
    repository = StubDeleteDocumentRepository(
        delete_results={
            "doc-local-only": True,
            "doc-local-and-vectors": True,
            "doc-vectors-only": False,
            "doc-graph-only": False,
            "doc-missing": False,
        }
    )
    weaviate = StubQdrantDeleteClient(
        deleted_chunks={
            "doc-local-only": 0,
            "doc-local-and-vectors": 4,
            "doc-vectors-only": 2,
            "doc-graph-only": 0,
            "doc-missing": 0,
        }
    )
    neo4j = StubNeo4jDeleteClient(
        deleted_nodes={
            "doc-local-only": 1,
            "doc-local-and-vectors": 5,
            "doc-vectors-only": 2,
            "doc-graph-only": 3,
            "doc-missing": 0,
        }
    )
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_qdrant_client] = lambda: weaviate
    app.dependency_overrides[get_neo4j_client] = lambda: neo4j

    response = client.post(
        "/documents/delete",
        json={
            "documentIds": [
                "doc-local-only",
                "doc-local-and-vectors",
                "doc-vectors-only",
                "doc-graph-only",
                "doc-missing",
            ]
        },
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "deletedCount": 4,
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
                "documentId": "doc-graph-only",
                "deleted": True,
                "deletedChunks": 0,
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
    weaviate = StubQdrantDeleteClient(deleted_chunks={"doc-1": 3})
    neo4j = StubNeo4jDeleteClient(deleted_nodes={"doc-1": 2})
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_qdrant_client] = lambda: weaviate
    app.dependency_overrides[get_neo4j_client] = lambda: neo4j

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


def test_delete_documents_returns_error_on_qdrant_failure():
    repository = StubDeleteDocumentRepository(delete_results={"doc-1": True})
    weaviate = StubQdrantDeleteClient(failing_document_ids={"doc-1"})
    neo4j = StubNeo4jDeleteClient(deleted_nodes={"doc-1": 4})
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_qdrant_client] = lambda: weaviate
    app.dependency_overrides[get_neo4j_client] = lambda: neo4j

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
                "error": "Failed to delete document records from Qdrant",
            }
        ],
    }
    assert repository.deleted_document_ids == []
    assert neo4j.deleted_document_ids == []


def test_delete_documents_returns_error_on_neo4j_failure():
    repository = StubDeleteDocumentRepository(delete_results={"doc-1": True})
    weaviate = StubQdrantDeleteClient(deleted_chunks={"doc-1": 3})
    neo4j = StubNeo4jDeleteClient(failing_document_ids={"doc-1"})
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_qdrant_client] = lambda: weaviate
    app.dependency_overrides[get_neo4j_client] = lambda: neo4j

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
                "deletedChunks": 3,
                "error": "Failed to delete document records from Neo4j",
            }
        ],
    }
    assert repository.deleted_document_ids == []


def test_hybrid_search_documents():
    weaviate = StubQdrantHybridSearchClient(
        results_by_document={
            "doc-1": [
                {
                    "chunk_id": "doc-1:2",
                    "document_id": "doc-1",
                    "section_path": "Access Control",
                    "text": "MFA is required for admins.",
                    "canonical_text": "mfa is required for admins.",
                    "node_type": "clause",
                    "markdown_text": "MFA is required for admins.",
                    "chunk_index": 2,
                    "_distance": 0.12,
                    "_score": 0.88,
                }
            ],
            "doc-2": [
                {
                    "chunk_id": "doc-2:4",
                    "document_id": "doc-2",
                    "section_path": "Authentication",
                    "text": "| Role | Requirement |\n| --- | --- |\n| User | MFA mandatory |",
                    "canonical_text": "role requirement user mfa mandatory",
                    "node_type": "table",
                    "markdown_text": "| Role | Requirement |\n| --- | --- |\n| User | MFA mandatory |",
                    "chunk_index": 4,
                    "_distance": 0.08,
                    "_score": 0.92,
                }
            ],
        }
    )
    app.dependency_overrides[get_qdrant_client] = lambda: weaviate

    response = client.post(
        "/documents/search/hybrid",
        json={
            "documentId1": "doc-1",
            "documentId2": "doc-2",
            "query": "mfa requirement",
            "limit": 2,
        },
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "query": "mfa requirement",
        "results": [
            {
                "documentId": "doc-1",
                "chunks": [
                    {
                        "chunkId": "doc-1:2",
                        "documentId": "doc-1",
                        "sectionPath": "Access Control",
                        "text": "mfa is required for admins.",
                        "nodeType": "clause",
                        "canonicalText": "mfa is required for admins.",
                        "markdown": "MFA is required for admins.",
                        "tableMarkdown": None,
                        "chunkIndex": 2,
                        "score": 0.88,
                        "distance": 0.12,
                        "scores": {"score": 0.88, "distance": 0.12},
                    }
                ],
            },
            {
                "documentId": "doc-2",
                "chunks": [
                    {
                        "chunkId": "doc-2:4",
                        "documentId": "doc-2",
                        "sectionPath": "Authentication",
                        "text": "role requirement user mfa mandatory",
                        "nodeType": "table",
                        "canonicalText": "role requirement user mfa mandatory",
                        "markdown": "| Role | Requirement |\n| --- | --- |\n| User | MFA mandatory |",
                        "tableMarkdown": "| Role | Requirement |\n| --- | --- |\n| User | MFA mandatory |",
                        "chunkIndex": 4,
                        "score": 0.92,
                        "distance": 0.08,
                        "scores": {"score": 0.92, "distance": 0.08},
                    }
                ],
            },
        ],
    }
    assert weaviate.calls == [
        {"query_string": "mfa requirement", "target_document_id": "doc-1", "limit": 2},
        {"query_string": "mfa requirement", "target_document_id": "doc-2", "limit": 2},
    ]


def test_hybrid_search_documents_rejects_invalid_payload():
    app.dependency_overrides[get_qdrant_client] = lambda: StubQdrantHybridSearchClient()
    try:
        response = client.post(
            "/documents/search/hybrid",
            json={
                "documentId1": "doc-1",
                "documentId2": "doc-1",
                "query": "   ",
            },
            headers=auth_headers(),
        )
        assert response.status_code == 400
        assert response.json() == {"detail": "documentId1 and documentId2 must be different"}
    finally:
        app.dependency_overrides.pop(get_qdrant_client, None)


def test_hybrid_search_documents_rejects_blank_query():
    app.dependency_overrides[get_qdrant_client] = lambda: StubQdrantHybridSearchClient()
    try:
        response = client.post(
            "/documents/search/hybrid",
            json={
                "documentId1": "doc-1",
                "documentId2": "doc-2",
                "query": "   ",
            },
            headers=auth_headers(),
        )
        assert response.status_code == 400
        assert response.json() == {"detail": "Query must not be empty"}
    finally:
        app.dependency_overrides.pop(get_qdrant_client, None)


def test_hybrid_search_documents_returns_error_on_qdrant_failure():
    weaviate = StubQdrantHybridSearchClient(should_fail=True)
    app.dependency_overrides[get_qdrant_client] = lambda: weaviate

    response = client.post(
        "/documents/search/hybrid",
        json={
            "documentId1": "doc-1",
            "documentId2": "doc-2",
            "query": "mfa",
        },
        headers=auth_headers(),
    )
    assert response.status_code == 502
    assert response.json() == {"detail": "Failed to run hybrid search in Qdrant"}


def test_compare_documents():
    app.dependency_overrides[get_diff_engine] = lambda: StubDiffEngine()
    response = client.post("/compare", json=compare_payload(), headers=auth_headers())
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"] == "Compared policy-v1 with policy-v2"
    assert payload["keyDifferences"][0]["changeType"] == "MODIFIED"


def test_compare_v5_enqueue_matches_v2_contract():
    dispatcher = StubCompareV2Dispatcher(enqueue_job_id="compare-v5-job-123")

    class StubCacheStore:
        def load_for_pair(self, *, doc1_id: str, doc2_id: str):
            return None

        def cached_job_id_for_pair(
            self,
            *,
            doc1_id: str,
            doc2_id: str,
            api_version: str = "v2",
            testing_department: str | None = None,
        ) -> str:
            return f"cached-{doc1_id}-{doc2_id}"

        def cache_key_for_pair(
            self,
            *,
            doc1_id: str,
            doc2_id: str,
            api_version: str = "v2",
            testing_department: str | None = None,
        ) -> str:
            return f"{api_version}:{testing_department or ''}:{doc1_id}:{doc2_id}"

    app.dependency_overrides[get_compare_v5_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_comparison_cache_store] = lambda: StubCacheStore()

    try:
        response = client.post(
            "/v5/compare",
            json=compare_payload(),
            headers=auth_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_compare_v5_dispatcher, None)
        app.dependency_overrides.pop(get_comparison_cache_store, None)

    assert response.status_code == 202
    assert response.json() == {
        "jobId": "compare-v5-job-123",
        "status": "queued",
        "cacheHit": False,
        "result": None,
    }
    assert len(dispatcher.enqueue_calls) == 1
    queued_payload = dispatcher.enqueue_calls[0]
    assert queued_payload.doc1.id == "policy-v1"
    assert queued_payload.doc2.id == "policy-v2"
    assert queued_payload.cache_key == "v5:EMC:policy-v1:policy-v2"
    assert queued_payload.api_version == "v5"
    assert queued_payload.testing_department == "EMC"
    assert queued_payload.save_to_db is False


def test_compare_v5_job_can_be_polled_from_v2_response_endpoint():
    dispatcher = StubCompareV2Dispatcher(
        enqueue_job_id="compare-v5-job-456",
        status_payload={
            "jobId": "compare-v5-job-456",
            "status": "queued",
            "done": False,
            "result": None,
            "error": None,
            "cacheHit": False,
        },
    )

    class StubCacheStore:
        def load_for_pair(self, *, doc1_id: str, doc2_id: str):
            return None

        def cached_job_id_for_pair(
            self,
            *,
            doc1_id: str,
            doc2_id: str,
            api_version: str = "v2",
            testing_department: str | None = None,
        ) -> str:
            return f"cached-{doc1_id}-{doc2_id}"

        def cache_key_for_pair(
            self,
            *,
            doc1_id: str,
            doc2_id: str,
            api_version: str = "v2",
            testing_department: str | None = None,
        ) -> str:
            return f"{api_version}:{testing_department or ''}:{doc1_id}:{doc2_id}"

    app.dependency_overrides[get_compare_v5_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_compare_v2_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_comparison_cache_store] = lambda: StubCacheStore()

    try:
        create_response = client.post(
            "/v5/compare",
            json=compare_payload(),
            headers=auth_headers(),
        )
        status_response = client.get(
            "/v2/compare/response/compare-v5-job-456",
            headers=auth_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_compare_v5_dispatcher, None)
        app.dependency_overrides.pop(get_compare_v2_dispatcher, None)
        app.dependency_overrides.pop(get_comparison_cache_store, None)

    assert create_response.status_code == 202
    assert create_response.json()["jobId"] == "compare-v5-job-456"
    assert status_response.status_code == 200
    assert status_response.json() == {
        "jobId": "compare-v5-job-456",
        "status": "queued",
        "done": False,
        "result": None,
        "error": None,
        "cacheHit": False,
    }
    assert dispatcher.status_calls == ["compare-v5-job-456"]


def test_compare_v5_dispatcher_requires_celery_queue(monkeypatch):
    monkeypatch.setattr(settings, "comparison_backend", "offline")
    dispatcher = api_deps.get_compare_v5_dispatcher()
    request = CompareRequest.model_validate(compare_payload())
    task_payload = CompareTaskPayload(
        doc1=request.doc1,
        doc2=request.doc2,
        force_re_extract=request.forceReExtract,
        cache_key="policy-v1:policy-v2",
        audit_mode=request.auditMode,
        save_to_db=request.saveToDb,
    )

    with pytest.raises(CeleryNotAvailableError, match="Celery queue is required"):
        dispatcher.enqueue_compare(task_payload)


def test_compare_v5_stream_by_id():
    stream_engine = StubDiffEngineStream()
    app.dependency_overrides[get_diff_engine_stream] = lambda: stream_engine
    app.dependency_overrides[get_document_repository] = lambda: StubCompareDocumentRepository()

    try:
        with client.stream(
            "POST",
            "/v5/compare/stream",
            json={
                "doc1Id": "doc-a",
                "doc2Id": "doc-b",
                "testingDepartment": "Environment",
            },
            headers=auth_headers(),
        ) as response:
            body = response.read().decode("utf-8")
    finally:
        app.dependency_overrides.pop(get_diff_engine_stream, None)
        app.dependency_overrides.pop(get_document_repository, None)

    assert response.status_code == 200
    assert "data:" in body
    assert '"type": "payload"' in body
    assert '"type": "done"' in body
    assert '"apiVersion": "v5"' in body
    assert '"serviceVersion": "compare-v5"' in body
    assert '"responseSchema": "comparison_stream_event_v5"' in body
    assert '"hybridSignals"' in body
    assert stream_engine.v4_calls == [
        {
            "doc1_id": "doc-a",
            "doc2_id": "doc-b",
            "force_re_extract": False,
            "testing_department": "Environment",
        }
    ]


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


def test_compare_v2_enqueue():
    dispatcher = StubCompareV2Dispatcher(enqueue_job_id="compare-job-123")

    class StubCacheStore:
        def load_for_pair(self, *, doc1_id: str, doc2_id: str):
            return None

        def cached_job_id_for_pair(
            self,
            *,
            doc1_id: str,
            doc2_id: str,
            api_version: str = "v2",
            testing_department: str | None = None,
        ) -> str:
            return f"cached-{doc1_id}-{doc2_id}"

        def cache_key_for_pair(
            self,
            *,
            doc1_id: str,
            doc2_id: str,
            api_version: str = "v2",
            testing_department: str | None = None,
        ) -> str:
            return f"{doc1_id}:{doc2_id}"

    app.dependency_overrides[get_compare_v2_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_comparison_cache_store] = lambda: StubCacheStore()

    response = client.post("/v2/compare", json=compare_payload(), headers=auth_headers())
    assert response.status_code == 202
    assert response.json() == {
        "jobId": "compare-job-123",
        "status": "queued",
        "cacheHit": False,
        "result": None,
    }
    assert len(dispatcher.enqueue_calls) == 1
    queued_payload = dispatcher.enqueue_calls[0]
    assert queued_payload.doc1.id == "policy-v1"
    assert queued_payload.doc2.id == "policy-v2"
    assert queued_payload.cache_key == "policy-v1:policy-v2"


def test_compare_v2_enqueue_returns_cached_result():
    dispatcher = StubCompareV2Dispatcher(enqueue_job_id="compare-job-123")
    cached_result = ComparisonResult(
        summary="cached",
        keyDifferences=[],
        actionPlan=[],
        followUpQuestions=[],
    )

    class StubCacheStore:
        def load_for_pair(self, *, doc1_id: str, doc2_id: str):
            return cached_result

        def cached_job_id_for_pair(
            self,
            *,
            doc1_id: str,
            doc2_id: str,
            api_version: str = "v2",
            testing_department: str | None = None,
        ) -> str:
            return f"cached-{doc1_id}-{doc2_id}"

        def cache_key_for_pair(
            self,
            *,
            doc1_id: str,
            doc2_id: str,
            api_version: str = "v2",
            testing_department: str | None = None,
        ) -> str:
            return f"{doc1_id}:{doc2_id}"

    app.dependency_overrides[get_compare_v2_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_comparison_cache_store] = lambda: StubCacheStore()

    response = client.post("/v2/compare", json=compare_payload(), headers=auth_headers())
    assert response.status_code == 202
    assert response.json() == {
        "jobId": "cached-policy-v1-policy-v2",
        "status": "finished",
        "cacheHit": True,
        "result": {
            "summary": "cached",
            "keyDifferences": [],
            "actionPlan": [],
            "followUpQuestions": [],
            "accuracyMetrics": None,
            "comparisonMode": "auditor_grade",
            "requireHumanReview": False,
            "hiddenDiffsCount": 0,
            "warnings": [],
            "suppressedDiffsCount": 0,
            "skippedSections": [],
        },
    }
    assert dispatcher.enqueue_calls == []


def test_compare_v2_response_by_path_param():
    dispatcher = StubCompareV2Dispatcher(
        status_payload={
            "jobId": "compare-job-456",
            "status": "finished",
            "done": True,
            "result": {
                "summary": "Compared policy-v1 with policy-v2",
                "keyDifferences": [],
                "actionPlan": [],
                "followUpQuestions": [],
                "accuracyMetrics": None,
            },
            "error": None,
            "cacheHit": False,
        }
    )
    app.dependency_overrides[get_compare_v2_dispatcher] = lambda: dispatcher

    response = client.get(
        "/v2/compare/response/compare-job-456",
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "jobId": "compare-job-456",
        "status": "finished",
        "done": True,
        "result": {
            "summary": "Compared policy-v1 with policy-v2",
            "keyDifferences": [],
            "actionPlan": [],
            "followUpQuestions": [],
            "accuracyMetrics": None,
            "comparisonMode": "auditor_grade",
            "requireHumanReview": False,
            "hiddenDiffsCount": 0,
            "warnings": [],
            "suppressedDiffsCount": 0,
            "skippedSections": [],
        },
        "error": None,
        "cacheHit": False,
    }
    assert dispatcher.status_calls == ["compare-job-456"]


def test_compare_v2_response_by_query_param():
    dispatcher = StubCompareV2Dispatcher(
        status_payload={
            "jobId": "compare-job-789",
            "status": "queued",
            "done": False,
            "result": None,
            "error": None,
            "cacheHit": False,
        }
    )
    app.dependency_overrides[get_compare_v2_dispatcher] = lambda: dispatcher

    response = client.get(
        "/v2/compare/response?jobid=compare-job-789",
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "jobId": "compare-job-789",
        "status": "queued",
        "done": False,
        "result": None,
        "error": None,
        "cacheHit": False,
    }
    assert dispatcher.status_calls == ["compare-job-789"]


def test_qdrant_dependency_closes_client(monkeypatch):
    closed = {"value": False}

    class StubQdrantVectorClient:
        def _client_get_collections(self):
            pass

        class _client:
            @staticmethod
            def get_collections():
                pass

        def close(self):
            closed["value"] = True

    monkeypatch.setattr("grc_policy_server.api.deps.QdrantVectorClient", StubQdrantVectorClient)

    dep = get_qdrant_client()
    _ = next(dep)
    with pytest.raises(StopIteration):
        next(dep)

    assert closed["value"] is True


def test_neo4j_dependency_closes_client(monkeypatch):
    closed = {"value": False}

    class StubNeo4jClient:
        def __init__(self, settings):
            self.settings = settings

        def close(self):
            closed["value"] = True

    monkeypatch.setattr("grc_policy_server.api.deps.Neo4jClient", StubNeo4jClient)
    monkeypatch.setattr(settings, "neo4j_enabled", True)

    dep = get_neo4j_client()
    _ = next(dep)
    with pytest.raises(StopIteration):
        next(dep)

    assert closed["value"] is True


def test_neo4j_dependency_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "neo4j_enabled", False)

    dep = get_neo4j_client()
    client = next(dep)
    with pytest.raises(StopIteration):
        next(dep)

    assert client is None
