from __future__ import annotations

from grc_policy_server.services.comparision.clause_matcher import (
    ClauseMatcher,
    MatchThresholds,
)
from grc_policy_server.services.ingestion.hierarchy_builder import (
    build_document_hierarchy,
)
from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk


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


def test_clause_matcher_uses_stable_ids_then_vector_fallback():
    def search_fn(**kwargs):
        query_text = kwargs["query_text"]
        if "vendors" in query_text.lower():
            return [
                {
                    "chunk_id": "right-2",
                    "stable_id": "stable-right-2",
                    "node_type": "clause",
                    "section_path": "Vendor Risk",
                    "text": "Vendors are reviewed every year.",
                    "_distance": 0.2,
                }
            ]
        return []

    matcher = ClauseMatcher(
        search_fn=search_fn,
        thresholds=MatchThresholds(),
        topk=3,
    )
    result = matcher.match(
        left_nodes=[
            {
                "chunk_id": "left-1",
                "stable_id": "stable-1",
                "node_type": "clause",
                "section_path": "Access Control",
                "text": "1.1 Multi-factor authentication is required for all admins.",
            },
            {
                "chunk_id": "left-2",
                "stable_id": "stable-left-2",
                "node_type": "clause",
                "section_path": "Vendor Risk",
                "text": "Vendors are reviewed annually.",
            },
        ],
        right_nodes=[
            {
                "chunk_id": "right-1",
                "stable_id": "stable-1",
                "node_type": "clause",
                "section_path": "Access Control",
                "text": "1.1 Multi-factor authentication is required for all admins and contractors.",
            },
            {
                "chunk_id": "right-2",
                "stable_id": "stable-right-2",
                "node_type": "clause",
                "section_path": "Vendor Risk",
                "text": "Vendors are reviewed every year.",
            },
        ],
        target_document_id="doc-right",
    )

    assert len(result.matches) == 2
    assert result.matches[0].matched_by == "stable_id"
    assert result.matches[1].matched_by == "vector_search"
    assert result.removed == []
    assert result.added == []
