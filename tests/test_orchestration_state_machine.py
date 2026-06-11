from __future__ import annotations

import pytest

from grc_policy_server.core.config import settings
from grc_policy_server.services.ingestion.document_ingestion_service import (
    DocumentIngestionService,
)
from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk
from grc_policy_server.services.orchestration.ingestion_orchestrator import (
    AgentFailure,
    ComplianceIngestionOrchestrator,
    DeterministicFailure,
    INGESTION_STAGES,
    IngestionToolRegistry,
)
from grc_policy_server.services.orchestration.job_state import IngestionState, JobState


def _noop_registry() -> IngestionToolRegistry:
    registry = IngestionToolRegistry()
    for stage in INGESTION_STAGES:
        registry.register(stage.name, lambda context: context)
    return registry


@pytest.mark.anyio
async def test_ingestion_orchestrator_routes_agent_failure_to_review() -> None:
    registry = _noop_registry()

    def fail_agent(context):
        raise AgentFailure("ambiguous ontology mapping")

    registry.register("map_ontology", fail_agent)
    job = JobState(job_id="job-1", doc_id="doc-1")

    final_job, context = await ComplianceIngestionOrchestrator(
        tool_registry=registry,
    ).run(job=job, context={"ok": True})

    assert context == {"ok": True}
    assert final_job.state == IngestionState.READY_FOR_COMPARISON
    assert final_job.progress == 100
    assert final_job.risk.review_required is True
    assert final_job.risk.review_reason == "ambiguous ontology mapping"


@pytest.mark.anyio
async def test_ingestion_orchestrator_halts_on_deterministic_failure() -> None:
    registry = _noop_registry()

    def fail_parse(context):
        raise ValueError("docling parse failed")

    registry.register("parse", fail_parse)
    job = JobState(job_id="job-1", doc_id="doc-1")

    with pytest.raises(DeterministicFailure):
        await ComplianceIngestionOrchestrator(tool_registry=registry).run(
            job=job,
            context={},
        )

    assert job.state == IngestionState.FAILED
    assert job.resume_from == "parse"
    assert job.progress == 7


@pytest.mark.anyio
async def test_document_ingestion_service_uses_state_machine_without_api_schema_changes(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "ontology_classification_enabled", False)

    class FakeStore:
        def __init__(self):
            self.saved = []

        def save_document(self, **kwargs):
            self.saved.append(kwargs)
            return []

    class FakeWeaviate:
        def __init__(self):
            self.records = []

        def upsert_chunks(self, records):
            self.records.extend(records)

    fake_store = FakeStore()
    fake_weaviate = FakeWeaviate()
    service = DocumentIngestionService(
        docling_adapter=None,  # type: ignore[arg-type]
        weaviate=fake_weaviate,  # type: ignore[arg-type]
        neo4j=None,
        llm=None,  # type: ignore[arg-type]
        upload_root=tmp_path,
        canonical_store=fake_store,  # type: ignore[arg-type]
    )

    async def fake_extract_parsed_chunks(*, filename: str, content: bytes):
        return (
            [
                ParsedChunk(
                    chunk_type="clause",
                    text="The device shall remain compliant at 30 MHz.",
                    section_path=("EMC",),
                    page_number=1,
                    ordinal=0,
                )
            ],
            {"enabled": False, "used": False},
            {"docling": "stub"},
        )

    monkeypatch.setattr(service, "_extract_parsed_chunks", fake_extract_parsed_chunks)

    result = await service.ingest_upload(
        filename="emc.pdf",
        content=b"%PDF-1.4 stub",
        content_type="application/pdf",
    )

    assert result.document_id
    assert result.chunks_stored > 0
    assert fake_store.saved
    assert fake_weaviate.records
    assert (tmp_path / result.document_id / "metadata.json").exists()

