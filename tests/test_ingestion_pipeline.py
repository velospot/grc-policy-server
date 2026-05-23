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
        weaviate=None,  # type: ignore[arg-type]
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

    class StubWeaviate:
        def __init__(self):
            self.records = []

        def upsert_chunks(self, records):
            self.records.extend(records)

    service = SectionSummaryBackfillService(
        upload_root=tmp_path,
        weaviate=StubWeaviate(),  # type: ignore[arg-type]
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
