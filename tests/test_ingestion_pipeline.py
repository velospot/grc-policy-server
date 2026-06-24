from __future__ import annotations

import json

import pytest

from grc_policy_server.services.comparison.clause_matcher import (
    ClauseMatcher,
    MatchThresholds,
)
from grc_policy_server.services.comparison.real_diff_engine import RealDiffEngine
from grc_policy_server.services.ingestion.hierarchy_builder import (
    build_document_hierarchy,
)
from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk
from grc_policy_server.services.ingestion.ocr_fallback import build_ocr_fallback_chunks
from grc_policy_server.services.ingestion.policy_preprocessor import (
    preprocess_parsed_chunks,
)
from grc_policy_server.services.ingestion.document_ingestion_service import (
    DocumentIngestionService,
    _IngestionContext,
)
from grc_policy_server.services.ingestion.section_summary_backfill import (
    SectionSummaryBackfillService,
)


def test_build_document_hierarchy_excludes_toc_from_indexing():
    hierarchy = build_document_hierarchy(
        document_id="doc-1",
        filename="policy-v1.pdf",
        content_hash="abc123",
        parsed_chunks=[
            ParsedChunk(
                chunk_type="heading",
                text="",
                section_path=("Contents",),
                page_number=1,
                ordinal=0,
                title="Contents",
            ),
            ParsedChunk(
                chunk_type="clause",
                text="1 Introduction .... 1\n2 Access Control .... 3\n3 Audit .... 5",
                section_path=("Contents",),
                page_number=1,
                ordinal=1,
                title=None,
            ),
            ParsedChunk(
                chunk_type="heading",
                text="",
                section_path=("Access Control",),
                page_number=2,
                ordinal=2,
                title="Access Control",
            ),
            ParsedChunk(
                chunk_type="clause",
                text="1.1 Multi-factor authentication is required for all admins.",
                section_path=("Access Control",),
                page_number=2,
                ordinal=3,
                title=None,
            ),
            ParsedChunk(
                chunk_type="table",
                text="Role, Requirement = Admin, MFA required",
                section_path=("Access Control",),
                page_number=2,
                ordinal=4,
                title="Authentication Matrix",
            ),
        ],
    )

    indexed_types = {(node.node_type, node.section_path) for node in hierarchy.indexable_nodes}
    assert ("clause", "Contents") not in indexed_types
    assert ("section", "Contents") not in indexed_types
    assert ("section", "Access Control") in indexed_types
    assert ("clause", "Access Control") in indexed_types
    assert ("table", "Access Control") in indexed_types

    toc_node = next(node for node in hierarchy.nodes if node.section_path == "Contents")
    assert toc_node.excluded_from_index is True
    assert toc_node.exclusion_reason == "table_of_contents"


@pytest.mark.anyio
async def test_build_relationships_keeps_upload_success_on_neo4j_failure(caplog, tmp_path):
    class FailingNeo4j:
        def upsert_document_hierarchy(self, **kwargs):
            raise RuntimeError("auth failed")

    service = DocumentIngestionService(
        docling_adapter=None,  # type: ignore[arg-type]
        qdrant=None,
        neo4j=FailingNeo4j(),  # type: ignore[arg-type]
        llm=None,  # type: ignore[arg-type]
        upload_root=tmp_path,
    )
    context = _IngestionContext(
        filename="policy.pdf",
        content=b"pdf",
        content_type="application/pdf",
        document_id="doc-1",
        content_hash="hash",
        normalized_tree={"nodes": [], "metadata": {}},
    )

    returned = await service._stage_build_relationships(context)

    assert returned is context
    assert "canonical nodes already saved, upload will succeed" in caplog.text
    assert "auth failed" in caplog.text


def test_build_document_hierarchy_uses_version_safe_stable_ids():
    chunks_v1 = [
        ParsedChunk(
            chunk_type="clause",
            text="1.1 Multi-factor authentication is required for all admins.",
            section_path=("Access Control",),
            page_number=2,
            ordinal=0,
        )
    ]
    chunks_v2 = [
        ParsedChunk(
            chunk_type="clause",
            text="1.1 Multi-factor authentication is required for all admins and vendors.",
            section_path=("Access Control",),
            page_number=2,
            ordinal=0,
        )
    ]
    hierarchy_v1 = build_document_hierarchy(
        document_id="doc-v1",
        filename="policy-v1.pdf",
        content_hash="hash-v1",
        parsed_chunks=chunks_v1,
    )
    hierarchy_v2 = build_document_hierarchy(
        document_id="doc-v2",
        filename="policy-v2.pdf",
        content_hash="hash-v2",
        parsed_chunks=chunks_v2,
    )

    clause_v1 = next(node for node in hierarchy_v1.nodes if node.node_type == "clause")
    clause_v2 = next(node for node in hierarchy_v2.nodes if node.node_type == "clause")

    assert clause_v1.stable_id == clause_v2.stable_id
    assert clause_v1.content_hash != clause_v2.content_hash
    assert clause_v1.node_id != clause_v2.node_id


def test_preprocess_parsed_chunks_filters_noise_and_merges_broken_paragraphs():
    processed = preprocess_parsed_chunks(
        [
            ParsedChunk(
                chunk_type="clause",
                text="1",
                section_path=("Access Control",),
                page_number=2,
                ordinal=0,
            ),
            ParsedChunk(
                chunk_type="clause",
                text="Privileged access should use",
                section_path=("Access Control",),
                page_number=2,
                ordinal=1,
            ),
            ParsedChunk(
                chunk_type="clause",
                text="mfa when remote.",
                section_path=("Access Control",),
                page_number=2,
                ordinal=2,
            ),
        ]
    )

    assert len(processed) == 1
    assert processed[0].text == "Privileged access should use mfa when remote."
    assert processed[0].metadata["clean_text"] == "privileged access should use mfa when remote."
    assert "obligation" not in processed[0].metadata


def test_build_document_hierarchy_carries_clean_text_and_clause_meaning():
    processed = [
        ParsedChunk(
            chunk_type="clause",
            text="1.1 Privileged access must use multi-factor authentication.",
            section_path=("Access Control",),
            page_number=2,
            ordinal=0,
            metadata={
                "clean_text": "privileged access must use mfa.",
                "obligation": "must",
                "subject": "privileged access",
                "action": "use",
                "object": "mfa",
                "condition": "",
            },
        )
    ]
    hierarchy = build_document_hierarchy(
        document_id="doc-1",
        filename="policy-v1.pdf",
        content_hash="hash-v1",
        parsed_chunks=processed,
    )

    clause = next(node for node in hierarchy.nodes if node.node_type == "clause")
    vector_record = clause.to_vector_record()

    assert vector_record["clean_text"] == "privileged access must use mfa."
    assert vector_record["obligation"] == "must"
    assert vector_record["subject"] == "privileged access"
    assert vector_record["action"] == "use"
    assert vector_record["object"] == "mfa"


def test_clause_matcher_aligns_sections_before_vector_fallback():
    search_calls = []

    def search_fn(**kwargs):
        search_calls.append(kwargs)
        return []

    matcher = ClauseMatcher(
        search_fn=search_fn,
        thresholds=MatchThresholds(),
        topk=3,
    )
    result = matcher.match(
        left_nodes=[
            {
                "chunk_id": "section-left-access",
                "stable_id": "section-access",
                "node_type": "section",
                "section_path": "Access Control",
                "title": "Access Control",
                "clean_text": "privileged access should use mfa",
                "page_number": 1,
                "chunk_index": 0,
            },
            {
                "chunk_id": "section-left-vendor",
                "stable_id": "section-vendor",
                "node_type": "section",
                "section_path": "Vendor Risk",
                "title": "Vendor Risk",
                "clean_text": "vendors are reviewed annually",
                "page_number": 2,
                "chunk_index": 0,
            },
            {
                "chunk_id": "left-1",
                "stable_id": "stable-1",
                "node_type": "clause",
                "section_path": "Access Control",
                "text": "1.1 Multi-factor authentication is required for all admins.",
                "clean_text": "mfa is required for all administrators.",
                "page_number": 1,
                "chunk_index": 1,
            },
            {
                "chunk_id": "left-2",
                "stable_id": "stable-left-2",
                "node_type": "clause",
                "section_path": "Vendor Risk",
                "text": "Vendors are reviewed annually.",
                "clean_text": "vendors are reviewed annually.",
                "page_number": 2,
                "chunk_index": 1,
            },
        ],
        right_nodes=[
            {
                "chunk_id": "section-right-vendor",
                "stable_id": "section-vendor",
                "node_type": "section",
                "section_path": "Vendor Risk",
                "title": "Vendor Risk",
                "clean_text": "vendors are reviewed every year",
                "page_number": 1,
                "chunk_index": 0,
            },
            {
                "chunk_id": "section-right-access",
                "stable_id": "section-access",
                "node_type": "section",
                "section_path": "Access Control",
                "title": "Access Control",
                "clean_text": "privileged access must use mfa",
                "page_number": 2,
                "chunk_index": 0,
            },
            {
                "chunk_id": "right-1",
                "stable_id": "stable-1",
                "node_type": "clause",
                "section_path": "Access Control",
                "text": "1.1 Multi-factor authentication is required for all admins and contractors.",
                "clean_text": "mfa is required for all administrators and contractors.",
                "page_number": 2,
                "chunk_index": 1,
            },
            {
                "chunk_id": "right-2",
                "stable_id": "stable-right-2",
                "node_type": "clause",
                "section_path": "Vendor Risk",
                "text": "Vendors are reviewed every year.",
                "clean_text": "vendors are reviewed every year.",
                "page_number": 1,
                "chunk_index": 1,
            },
        ],
        target_document_id="doc-right",
    )

    assert len(result.matches) == 2
    assert result.matches[0].matched_by == "stable_id"
    assert result.matches[1].matched_by == "section_stable_id"
    assert result.removed == []
    assert result.added == []
    assert search_calls == []


@pytest.mark.anyio
async def test_real_diff_engine_enriches_missing_semantics_rule_based():
    # _enrich_nodes_with_semantics now uses rule-based extraction only (no LLM).
    # Use English text so the obligation verb regex matches reliably.
    engine = RealDiffEngine(
        qdrant=None,  # type: ignore[arg-type]
        neo4j=None,  # type: ignore[arg-type]
        llm=None,  # type: ignore[arg-type]
    )

    enriched = await engine._enrich_nodes_with_semantics(
        [
            {
                "chunk_id": "clause-1",
                "node_type": "clause",
                "text": "Privileged access must use MFA.",
                "section_path": "Access Control",
            }
        ]
    )

    assert enriched[0]["clean_text"] == "privileged access must use mfa."
    assert enriched[0]["obligation"] == "must"


def test_ocr_fallback_skips_when_tesseract_binary_missing(monkeypatch):
    monkeypatch.setattr("grc_policy_server.services.ingestion.ocr_fallback.shutil.which", lambda _: None)

    chunks, metadata, pages = build_ocr_fallback_chunks(
        filename="policy.pdf",
        content=b"%PDF-1.4",
        parsed_chunks=[
            ParsedChunk(
                chunk_type="clause",
                text="short",
                section_path=("Access Control",),
                page_number=1,
                ordinal=0,
            )
        ],
        page_count=1,
        min_chars_per_page=80,
        min_total_chars=250,
        render_dpi=180,
        languages="eng",
        page_segmentation_mode=6,
    )

    assert chunks == []
    assert pages == set()
    assert metadata["enabled"] is True
    assert metadata["used"] is False
    assert metadata["reason"] == "missing_tesseract_binary"


def test_section_summary_backfill_updates_existing_hierarchy(tmp_path):
    document_dir = tmp_path / "doc-1"
    document_dir.mkdir()
    (document_dir / "metadata.json").write_text(
        json.dumps({"id": "doc-1", "name": "policy.pdf"}),
        encoding="utf-8",
    )
    (document_dir / "hierarchy.json").write_text(
        json.dumps(
            {
                "documentStableId": "stable-doc-1",
                "documentFamily": "policy",
                "contentHash": "hash-1",
                "metadata": {},
                "nodes": [
                    {
                        "node_id": "section-1",
                        "stable_id": "section-stable-1",
                        "content_hash": "section-hash",
                        "document_id": "doc-1",
                        "document_stable_id": "stable-doc-1",
                        "node_type": "section",
                        "parent_id": "doc-1",
                        "title": "Access Control",
                        "text": "admins must use mfa.",
                        "section_path": "Access Control",
                        "section_titles": ["Access Control"],
                        "page_number": 1,
                        "ordinal": 1,
                        "indexable": True,
                        "excluded_from_index": False,
                        "exclusion_reason": None,
                        "source": "docling",
                        "lineage": ["Access Control"],
                        "lineage_ids": ["doc-1", "section-1"],
                        "metadata": {},
                    },
                    {
                        "node_id": "clause-1",
                        "stable_id": "clause-stable-1",
                        "content_hash": "clause-hash",
                        "document_id": "doc-1",
                        "document_stable_id": "stable-doc-1",
                        "node_type": "clause",
                        "parent_id": "section-1",
                        "title": None,
                        "text": "Admins must use MFA every 12 months.",
                        "section_path": "Access Control",
                        "section_titles": ["Access Control"],
                        "page_number": 1,
                        "ordinal": 2,
                        "indexable": True,
                        "excluded_from_index": False,
                        "exclusion_reason": None,
                        "source": "docling",
                        "lineage": ["Access Control"],
                        "lineage_ids": ["doc-1", "section-1"],
                        "metadata": {"clean_text": "admins must use mfa every 12 months."},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    class StubQdrant:
        def __init__(self):
            self.records = []

        def upsert_chunks(self, records):
            self.records.extend(records)

    service = SectionSummaryBackfillService(
        upload_root=tmp_path,
        qdrant=StubQdrant(),  # type: ignore[arg-type]
        neo4j=None,
    )

    result = service.backfill_all()
    hierarchy = json.loads((document_dir / "hierarchy.json").read_text(encoding="utf-8"))
    section_node = next(node for node in hierarchy["nodes"] if node["node_type"] == "section")

    assert result.documents_seen == 1
    assert result.documents_updated == 1
    assert result.section_nodes_updated == 1
    assert "section_summary_backfill_at" in hierarchy["metadata"]
    assert section_node["metadata"]["summary_text"] == "admins must use mfa every 12 months."
    assert section_node["metadata"]["summary_numbers"] == ["12"]


def test_clause_matcher_calls_search_fn_for_unmatched_nodes():
    """search_fn is invoked for each unmatched left node in a mapped section."""
    search_calls: list[dict] = []

    def search_fn(**kwargs):
        search_calls.append(kwargs)
        return []

    matcher = ClauseMatcher(search_fn=search_fn, thresholds=MatchThresholds(), topk=3)
    result = matcher.match(
        left_nodes=[
            # Section node — establishes the section in section_map via stable_id
            {
                "chunk_id": "sec-l-safety",
                "stable_id": "sec-safety",
                "node_type": "section",
                "section_path": "5.1 Safety Requirements",
                "title": "5.1 Safety Requirements",
                "clean_text": "safety requirements",
                "page_number": 1,
                "chunk_index": 0,
            },
            # L1 — matched to R1 by stable_id
            {
                "chunk_id": "l-1",
                "stable_id": "sid-matched",
                "node_type": "clause",
                "section_path": "5.1 Safety Requirements",
                "text": "Admins must use multi-factor authentication.",
                "clean_text": "admins must use multi-factor authentication.",
                "page_number": 1,
                "chunk_index": 1,
            },
            # L2 — no stable_id, low text similarity to any right node
            {
                "chunk_id": "l-2",
                "node_type": "clause",
                "section_path": "5.1 Safety Requirements",
                "text": "Capacitor discharge rate shall not exceed 10 nanofarads.",
                "clean_text": "capacitor discharge rate shall not exceed 10 nanofarads.",
                "page_number": 1,
                "chunk_index": 2,
            },
            # L3 — no stable_id, low text similarity to any right node
            {
                "chunk_id": "l-3",
                "node_type": "clause",
                "section_path": "5.1 Safety Requirements",
                "text": "All connectors must pass IP67 ingress protection testing.",
                "clean_text": "all connectors must pass ip67 ingress protection testing.",
                "page_number": 2,
                "chunk_index": 3,
            },
        ],
        right_nodes=[
            {
                "chunk_id": "sec-r-safety",
                "stable_id": "sec-safety",
                "node_type": "section",
                "section_path": "5.1 Safety Requirements",
                "title": "5.1 Safety Requirements",
                "clean_text": "safety requirements",
                "page_number": 1,
                "chunk_index": 0,
            },
            # R1 — matched to L1 by stable_id
            {
                "chunk_id": "r-1",
                "stable_id": "sid-matched",
                "node_type": "clause",
                "section_path": "5.1 Safety Requirements",
                "text": "Administrators must use MFA for all logins.",
                "clean_text": "administrators must use mfa for all logins.",
                "page_number": 1,
                "chunk_index": 1,
            },
            # R2 — no match candidate for L2 or L3 (unrelated vocabulary)
            {
                "chunk_id": "r-2",
                "node_type": "clause",
                "section_path": "5.1 Safety Requirements",
                "text": "Mechanical vibration resistance per IEC 60068-2-6.",
                "clean_text": "mechanical vibration resistance per iec 60068-2-6.",
                "page_number": 2,
                "chunk_index": 2,
            },
        ],
        target_document_id="doc-right",
    )

    # L1 matched to R1; L2 and L3 are unmatched (2 left, 1 right → no orphan fallback)
    assert any(m.left["chunk_id"] == "l-1" and m.right["chunk_id"] == "r-1" for m in result.matches)
    unmatched_left_ids = {n["chunk_id"] for n in result.removed}
    assert "l-2" in unmatched_left_ids
    assert "l-3" in unmatched_left_ids
    assert any(n["chunk_id"] == "r-2" for n in result.added)
    # search_fn invoked once per unmatched left node in the mapped section
    assert len(search_calls) == 2
    queried_ids = {c.get("query_text") for c in search_calls}
    assert any("capacitor" in (t or "") for t in queried_ids)
    assert any("ip67" in (t or "").lower() for t in queried_ids)


def test_clause_matcher_stable_id_priority_in_matched_section():
    """stable_id match takes priority over high lexical similarity within a section."""
    matcher = ClauseMatcher(search_fn=None, thresholds=MatchThresholds(), topk=3)
    result = matcher.match(
        left_nodes=[
            {
                "chunk_id": "sec-l",
                "stable_id": "sec-req",
                "node_type": "section",
                "section_path": "5.1 Requirements",
                "title": "5.1 Requirements",
                "clean_text": "requirements",
                "page_number": 1,
                "chunk_index": 0,
            },
            {
                "chunk_id": "l-1",
                "stable_id": "shared-sid",
                "node_type": "clause",
                "section_path": "5.1 Requirements",
                "text": "Voltage must be 12V DC with tolerance 5%.",
                "clean_text": "voltage must be 12v dc with tolerance 5%.",
                "page_number": 1,
                "chunk_index": 1,
            },
        ],
        right_nodes=[
            {
                "chunk_id": "sec-r",
                "stable_id": "sec-req",
                "node_type": "section",
                "section_path": "5.1 Requirements",
                "title": "5.1 Requirements",
                "clean_text": "requirements",
                "page_number": 1,
                "chunk_index": 0,
            },
            # R1 — stable_id matches L1; text is different
            {
                "chunk_id": "r-1",
                "stable_id": "shared-sid",
                "node_type": "clause",
                "section_path": "5.1 Requirements",
                "text": "DC supply voltage: 12 Volt ±5% tolerance.",
                "clean_text": "dc supply voltage 12 volt 5 percent tolerance.",
                "page_number": 1,
                "chunk_index": 1,
            },
            # R2 — different stable_id but identical text to L1 (lexically perfect match)
            {
                "chunk_id": "r-2",
                "stable_id": "other-sid",
                "node_type": "clause",
                "section_path": "5.1 Requirements",
                "text": "Voltage must be 12V DC with tolerance 5%.",
                "clean_text": "voltage must be 12v dc with tolerance 5%.",
                "page_number": 2,
                "chunk_index": 2,
            },
        ],
        target_document_id="doc-right",
    )

    # L1 must match R1 (via stable_id), not R2 (via lexical similarity)
    assert len(result.matches) == 1
    assert result.matches[0].matched_by == "stable_id"
    assert result.matches[0].left["chunk_id"] == "l-1"
    assert result.matches[0].right["chunk_id"] == "r-1"
    # R2 is left unmatched (ADDED) because L1 was already consumed by stable_id match
    assert len(result.added) == 1
    assert result.added[0]["chunk_id"] == "r-2"
    assert result.removed == []


def test_compare_v5_service_decorates_done_event_with_hybrid_signals():
    """done SSE event contains hybridSignals.matchingStrategy; non-done events do not."""
    from grc_policy_server.services.comparison.compare_v5_service import CompareV5Service

    class _StreamEngine:
        qdrant = object()  # truthy → qdrantAvailable = True
        neo4j = None       # falsy  → neo4jAvailable = False
        canonical_store = object()

    class _DocRef:
        def __init__(self, id_: str):
            self.id = id_

    class _Payload:
        testingDepartment = "Safety"

    service = CompareV5Service(document_repo=None, stream_engine=_StreamEngine())  # type: ignore[arg-type]
    doc1, doc2, payload = _DocRef("doc-1"), _DocRef("doc-2"), _Payload()

    done_event = service._decorate_stream_event(
        {"type": "done"}, doc1=doc1, doc2=doc2, payload=payload  # type: ignore[arg-type]
    )
    assert "hybridSignals" in done_event
    assert done_event["hybridSignals"]["matchingStrategy"] == "canonical+qdrant+neo4j_graph_fallback"
    assert done_event["hybridSignals"]["qdrantAvailable"] is True
    assert done_event["hybridSignals"]["neo4jAvailable"] is False
    assert done_event["hybridSignals"]["canonicalStoreAvailable"] is True
    assert done_event["apiVersion"] == "v5"
    assert done_event["serviceVersion"] == "compare-v5"

    progress_event = service._decorate_stream_event(
        {"type": "progress", "pct": 50}, doc1=doc1, doc2=doc2, payload=payload  # type: ignore[arg-type]
    )
    assert "hybridSignals" not in progress_event
    assert progress_event["apiVersion"] == "v5"
