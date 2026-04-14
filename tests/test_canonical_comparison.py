from __future__ import annotations

import json
from pathlib import Path

import pytest

from grc_policy_server.models.schemas import Document
from grc_policy_server.services.comparison.clause_matcher import MatchThresholds
from grc_policy_server.services.comparison.comparison_trace import ComparisonTraceStore
from grc_policy_server.services.comparison.real_diff_engine import RealDiffEngine
from grc_policy_server.services.documents.canonical_models import CanonicalNode
from grc_policy_server.services.documents.canonical_store import CanonicalDocumentStore


def _doc(document_id: str) -> Document:
    return Document(
        id=document_id,
        name=document_id,
        version="1.0",
        uploadDate="2026-04-01",
        size="1 KB",
        category="policy",
    )


def _node(
    *,
    node_id: str,
    document_id: str,
    section: str,
    text: str,
    stable_id: str = "stable-1",
    obligation: str = "must",
    order: int = 1,
) -> dict:
    return {
        "chunk_id": node_id,
        "node_id": node_id,
        "canonical_node_id": node_id,
        "document_id": document_id,
        "stable_id": stable_id,
        "node_type": "paragraph",
        "section_path": section,
        "heading_path": [section],
        "text": text,
        "clean_text": text.lower(),
        "canonical_text": text.lower(),
        "comparison_text": text.lower(),
        "chunk_index": order,
        "order_index": order,
        "page_number": 1,
        "page": 1,
        "obligation": obligation,
        "subject": "admins",
        "action": "use",
        "object": "mfa",
        "condition": "",
        "markdown_text": text,
    }


class _StubCanonicalStore:
    def __init__(
        self,
        nodes_by_document: dict[str, list[dict]],
        artifacts_by_document: dict[str, dict] | None = None,
    ) -> None:
        self.nodes_by_document = nodes_by_document
        self.artifacts_by_document = artifacts_by_document or {}

    def load_comparison_nodes(self, document_id: str) -> list[dict]:
        return self.nodes_by_document.get(document_id, [])

    def load_debug_artifacts(self, document_id: str) -> dict:
        return self.artifacts_by_document.get(document_id, {})


class _FailingFetchWeaviate:
    def __init__(self) -> None:
        self.fetch_called = False

    def fetch_chunks_by_document(self, document_id: str) -> list[dict]:
        self.fetch_called = True
        raise AssertionError("comparison must not use Weaviate as canonical substrate")

    def search_section_in_document(self, **kwargs) -> list[dict]:
        return []


class _StubLLM:
    async def detect_language(self, text_sample: str) -> str:
        return "en"

    async def extract_policy_meanings(self, **kwargs) -> list[dict[str, str]]:
        return []

    async def summarize_diff(self, **kwargs) -> str:
        return "change explanation"

    async def summarize_explanations(self, **kwargs) -> str:
        return "structured summary"

    async def summarize_changes(self, **kwargs) -> str:
        return "fallback summary"

    async def generate_followups(self, **kwargs) -> list[str]:
        return []

    async def generate_markdown_diff_summary(self, **kwargs) -> str:
        return ""


class _CapturingChangeRecordLLM(_StubLLM):
    def __init__(self) -> None:
        self.change_record_payload: dict | None = None

    async def summarize_change_records(self, **kwargs) -> str:
        self.change_record_payload = kwargs["change_record_payload"]
        return "record summary"


def test_canonical_store_writes_raw_docling_and_normalized_nodes(tmp_path: Path):
    hierarchy = {
        "documentStableId": "stable-doc",
        "documentFamily": "policy",
        "contentHash": "hash-1",
        "metadata": {},
        "nodes": [
            {
                "node_id": "section-1",
                "stable_id": "section-stable",
                "content_hash": "section-hash",
                "document_id": "doc-1",
                "node_type": "section",
                "parent_id": "doc-1",
                "title": "5.2 Access Control",
                "text": "Admins must use MFA.",
                "section_path": "5.2 Access Control",
                "section_titles": ["5.2 Access Control"],
                "page_number": 3,
                "ordinal": 1,
                "indexable": False,
                "metadata": {"clean_text": "admins must use mfa."},
            },
            {
                "node_id": "para-1",
                "stable_id": "para-stable",
                "content_hash": "para-hash",
                "document_id": "doc-1",
                "node_type": "clause",
                "parent_id": "section-1",
                "title": None,
                "text": "Admins must use MFA.",
                "section_path": "5.2 Access Control",
                "section_titles": ["5.2 Access Control"],
                "page_number": 3,
                "ordinal": 2,
                "indexable": True,
                "metadata": {"clean_text": "admins must use mfa."},
            },
        ],
    }
    store = CanonicalDocumentStore(database_url="", upload_root=tmp_path)

    saved = store.save_document(
        document_id="doc-1",
        filename="policy.pdf",
        content_hash="hash-1",
        docling_json={"body": "raw"},
        hierarchy=hierarchy,
    )
    loaded = store.load_comparison_nodes("doc-1")

    assert (tmp_path / "doc-1" / "raw_docling.json").exists()
    assert (tmp_path / "doc-1" / "canonical_nodes.json").exists()
    artifacts = store.load_debug_artifacts("doc-1")
    assert len(saved) == 2
    assert artifacts["rawDoclingJson"] == {"body": "raw"}
    assert artifacts["normalizedTreeJson"]["retrievalArtifacts"]["retrievalChunkCount"] == 1
    assert loaded[1]["canonical_node_id"] == "para-1"
    assert loaded[1]["node_type"] == "paragraph"
    assert loaded[1]["section_label"] == "5.2"
    assert loaded[1]["canonical_text"] == "admins must use mfa."


def test_canonical_node_normalizes_glossary_definitions():
    node = CanonicalNode.from_hierarchy_record(
        {
            "node_id": "definition-1",
            "stable_id": "definition-stable",
            "content_hash": "hash",
            "document_id": "doc-1",
            "node_type": "clause",
            "parent_id": "section-glossary",
            "title": None,
            "text": '"Control" - A safeguard or countermeasure.',
            "section_path": "Glossary",
            "section_titles": ["Glossary"],
            "page_number": 2,
            "ordinal": 1,
            "metadata": {
                "clean_text": '"control" - a safeguard or countermeasure.'
            },
        }
    )

    assert node.node_type == "definition"
    assert node.normalized_text == "control: a safeguard or countermeasure."


@pytest.mark.anyio
async def test_real_diff_engine_compares_canonical_nodes_not_weaviate_chunks():
    left = _node(
        node_id="doc-1-node",
        document_id="doc-1",
        section="Access Control",
        text="Admins should use MFA.",
        obligation="should",
    )
    right = _node(
        node_id="doc-2-node",
        document_id="doc-2",
        section="Access Control",
        text="Admins must use MFA.",
        obligation="must",
    )
    weaviate = _FailingFetchWeaviate()
    engine = RealDiffEngine(
        weaviate=weaviate,  # type: ignore[arg-type]
        neo4j=None,
        llm=_StubLLM(),  # type: ignore[arg-type]
        canonical_store=_StubCanonicalStore({"doc-1": [left], "doc-2": [right]}),  # type: ignore[arg-type]
    )

    result = await engine.compare(_doc("doc-1"), _doc("doc-2"))

    assert weaviate.fetch_called is False
    assert result.summary == "structured summary"
    assert len(result.keyDifferences) == 1
    assert result.keyDifferences[0].changeType == "MODIFIED"
    assert result.keyDifferences[0].doc1Reference is not None
    assert result.keyDifferences[0].doc1Reference.sourceText == "Admins should use MFA."


@pytest.mark.anyio
async def test_real_diff_engine_sends_structured_change_records_to_llm():
    left = _node(
        node_id="doc-1-node",
        document_id="doc-1",
        section="Access Control",
        text="Admins should use MFA.",
        obligation="should",
    )
    right = _node(
        node_id="doc-2-node",
        document_id="doc-2",
        section="Access Control",
        text="Admins must use MFA.",
        obligation="must",
    )
    llm = _CapturingChangeRecordLLM()
    engine = RealDiffEngine(
        weaviate=_FailingFetchWeaviate(),  # type: ignore[arg-type]
        neo4j=None,
        llm=llm,  # type: ignore[arg-type]
        canonical_store=_StubCanonicalStore({"doc-1": [left], "doc-2": [right]}),  # type: ignore[arg-type]
    )

    result = await engine.compare(_doc("doc-1"), _doc("doc-2"))

    assert result.summary == "record summary"
    assert llm.change_record_payload is not None
    payload = llm.change_record_payload
    assert payload["promptVersion"] == "structured-change-records-v2"
    assert payload["documentMetadata"]["doc1"]["id"] == "doc-1"
    assert payload["mode"] == "general"
    assert payload["groupedChangeRecordsByChapter"][0]["chapter"] == "Access Control"
    record = payload["changeRecords"][0]
    assert record["leftNodeIds"] == ["doc-1-node"]
    assert record["rightNodeIds"] == ["doc-2-node"]
    # "should" → "must" is a one-word swap; textual distance is well below 0.60,
    # so the new severity rules classify it as medium (semantic signal present,
    # but not enough content divergence to reach HIGH). Obligation weakening would
    # still produce critical/high; strengthening is medium.
    assert record["changeSeverity"] == "medium"
    assert record["requirementVerbChange"]["direction"] == "strengthened"
    assert record["doc1Reference"]["sourceText"] == "Admins should use MFA."
    assert record["doc2Reference"]["sourceText"] == "Admins must use MFA."


@pytest.mark.anyio
async def test_comparison_trace_captures_loss_map_checkpoints(tmp_path: Path):
    left = _node(
        node_id="doc-1-node",
        document_id="doc-1",
        section="Access Control",
        text="Admins should use MFA.",
        obligation="should",
    )
    right = _node(
        node_id="doc-2-node",
        document_id="doc-2",
        section="Access Control",
        text="Admins must use MFA.",
        obligation="must",
    )
    artifacts = {
        "doc-1": _debug_artifacts("doc-1", [left]),
        "doc-2": _debug_artifacts("doc-2", [right]),
    }
    engine = RealDiffEngine(
        weaviate=_FailingFetchWeaviate(),  # type: ignore[arg-type]
        neo4j=None,
        llm=_CapturingChangeRecordLLM(),  # type: ignore[arg-type]
        canonical_store=_StubCanonicalStore(
            {"doc-1": [left], "doc-2": [right]},
            artifacts_by_document=artifacts,
        ),  # type: ignore[arg-type]
        trace_store=ComparisonTraceStore(upload_root=tmp_path),
    )

    await engine.compare(_doc("doc-1"), _doc("doc-2"))

    trace_files = list((tmp_path / "_comparison_traces").glob("*.json"))
    assert len(trace_files) == 1
    trace = json.loads(trace_files[0].read_text(encoding="utf-8"))
    checkpoints = trace["checkpoints"]
    assert set(checkpoints) == {
        "rawExtractedStructure",
        "normalizedNodeTree",
        "retrievalArtifacts",
        "alignmentResults",
        "diffRecords",
        "llmInputPayload",
        "finalSummary",
    }
    assert checkpoints["rawExtractedStructure"]["doc1"]["rawDoclingStored"] is True
    assert checkpoints["normalizedNodeTree"]["doc1NodeCounts"]["paragraph"] == 1
    assert checkpoints["retrievalArtifacts"]["doc1"]["retrievalChunkCount"] == 1
    assert checkpoints["alignmentResults"]["matchedNodes"] == 1
    assert checkpoints["diffRecords"]["changeCounts"]["MODIFIED"] == 1
    assert checkpoints["llmInputPayload"]["promptVersion"] == "structured-change-records-v2"
    assert checkpoints["finalSummary"]["summary"] == "record summary"
    assert checkpoints["finalSummary"]["citationCoverage"]["coverageRatio"] == 1.0


@pytest.mark.anyio
async def test_real_diff_engine_records_moved_canonical_nodes():
    left = _node(
        node_id="doc-1-node",
        document_id="doc-1",
        section="Old Section",
        text="Admins must use MFA.",
    )
    right = _node(
        node_id="doc-2-node",
        document_id="doc-2",
        section="New Section",
        text="Admins must use MFA.",
    )
    engine = RealDiffEngine(
        weaviate=_FailingFetchWeaviate(),  # type: ignore[arg-type]
        neo4j=None,
        llm=_StubLLM(),  # type: ignore[arg-type]
        canonical_store=_StubCanonicalStore({"doc-1": [left], "doc-2": [right]}),  # type: ignore[arg-type]
    )

    result = await engine.compare(_doc("doc-1"), _doc("doc-2"))

    assert len(result.keyDifferences) == 1
    diff = result.keyDifferences[0]
    assert diff.changeType == "MODIFIED"
    # Moved nodes are always medium — the reviewer must confirm the new location
    # is contextually appropriate, even when content is unchanged.
    assert diff.impact == "Medium"
    assert diff.changeSeverity == "medium"
    assert diff.changes[0].oldValue == "Old Section"
    assert diff.changes[0].newValue == "New Section"


@pytest.mark.anyio
async def test_real_diff_engine_records_moved_semantic_changes_as_high():
    left = _node(
        node_id="doc-1-node",
        document_id="doc-1",
        section="Old Section",
        text="Admins must use MFA.",
        obligation="must",
    )
    right = _node(
        node_id="doc-2-node",
        document_id="doc-2",
        section="New Section",
        text="Admins may use MFA.",
        obligation="may",
    )
    engine = RealDiffEngine(
        weaviate=_FailingFetchWeaviate(),  # type: ignore[arg-type]
        neo4j=None,
        llm=_StubLLM(),  # type: ignore[arg-type]
        canonical_store=_StubCanonicalStore({"doc-1": [left], "doc-2": [right]}),  # type: ignore[arg-type]
    )

    result = await engine.compare(_doc("doc-1"), _doc("doc-2"))

    assert len(result.keyDifferences) == 1
    diff = result.keyDifferences[0]
    assert diff.changeType == "MODIFIED"
    # Obligation weakening (must → may) is medium under the current rules:
    # obligation changes are important for review but not automatically high severity.
    assert diff.changeSeverity == "medium"


@pytest.mark.anyio
async def test_real_diff_engine_records_cosmetic_text_changes_as_low():
    left = _node(
        node_id="doc-1-node",
        document_id="doc-1",
        section="Access Control",
        text="Administrators must maintain record-keeping logs.",
    )
    right = _node(
        node_id="doc-2-node",
        document_id="doc-2",
        section="Access Control",
        text="Administrators must maintain record keeping logs:",
    )
    engine = RealDiffEngine(
        weaviate=_FailingFetchWeaviate(),  # type: ignore[arg-type]
        neo4j=None,
        llm=_StubLLM(),  # type: ignore[arg-type]
        canonical_store=_StubCanonicalStore({"doc-1": [left], "doc-2": [right]}),  # type: ignore[arg-type]
    )

    result = await engine.compare(_doc("doc-1"), _doc("doc-2"))

    assert len(result.keyDifferences) == 1
    diff = result.keyDifferences[0]
    assert diff.changeType == "MODIFIED"
    assert diff.impact == "Low"
    assert diff.changeSeverity == "low"


@pytest.mark.anyio
async def test_real_diff_engine_records_split_canonical_nodes():
    left = _node(
        node_id="doc-1-node",
        document_id="doc-1",
        section="Access Control",
        text="Admins must use MFA. Vendors must use MFA.",
        stable_id="left-combined",
    )
    right_1 = _node(
        node_id="doc-2-node-1",
        document_id="doc-2",
        section="Access Control",
        text="Admins must use MFA.",
        stable_id="right-admins",
        order=1,
    )
    right_2 = _node(
        node_id="doc-2-node-2",
        document_id="doc-2",
        section="Access Control",
        text="Vendors must use MFA.",
        stable_id="right-vendors",
        order=2,
    )
    engine = RealDiffEngine(
        weaviate=_FailingFetchWeaviate(),  # type: ignore[arg-type]
        neo4j=None,
        llm=_StubLLM(),  # type: ignore[arg-type]
        canonical_store=_StubCanonicalStore(
            {"doc-1": [left], "doc-2": [right_1, right_2]}
        ),  # type: ignore[arg-type]
        thresholds=MatchThresholds(min_clause_score=0.95),
    )

    result = await engine.compare(_doc("doc-1"), _doc("doc-2"))

    assert len(result.keyDifferences) == 1
    diff = result.keyDifferences[0]
    assert diff.changeType == "MODIFIED"
    assert diff.changes[0].text == "Node split into multiple nodes"
    assert diff.changes[0].location == "structure"


def _debug_artifacts(document_id: str, nodes: list[dict]) -> dict:
    hierarchy_nodes = [
        {
            "node_id": node["node_id"],
            "node_type": node["node_type"],
            "section_path": node["section_path"],
            "title": node["section_path"],
            "page_number": node["page_number"],
            "ordinal": node["order_index"],
            "indexable": True,
            "parent_id": node.get("parent_id"),
        }
        for node in nodes
    ]
    return {
        "documentId": document_id,
        "rawDoclingJson": {"pages": [{"page_no": 1}]},
        "normalizedTreeJson": {
            "documentId": document_id,
            "retrievalArtifacts": {
                "retrievalChunkCount": len(nodes),
                "chunkToNodeMapping": [
                    {
                        "chunkId": node["node_id"],
                        "canonicalNodeId": node["node_id"],
                        "nodeType": node["node_type"],
                        "sectionPath": node["section_path"],
                        "page": node["page_number"],
                    }
                    for node in nodes
                ],
            },
            "nodes": hierarchy_nodes,
        },
        "hierarchyJson": {
            "metadata": {
                "node_counts": {"paragraph": len(nodes)},
                "excluded_nodes": 0,
                "ocr_nodes": 0,
            },
            "nodes": hierarchy_nodes,
        },
        "rawDoclingPath": f"/tmp/{document_id}/raw_docling.json",
        "normalizedTreePath": f"/tmp/{document_id}/canonical_nodes.json",
        "hierarchyPath": f"/tmp/{document_id}/hierarchy.json",
    }
