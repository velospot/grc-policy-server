"""Tests for table identity resolver (split/continued table detection)."""

import pytest

from grc_policy_server.services.ingestion.table_extraction_ensemble import TableCandidate
from grc_policy_server.services.ingestion.table_identity_resolver import (
    TableIdentity,
    TableIdentityResolver,
)


class TestTableIdentity:
    """Test TableIdentity data class."""

    def test_identity_creation(self) -> None:
        """Test creating a table identity."""
        identity = TableIdentity(
            table_uid="tbl_access_control_001",
            caption_original="Table 5: Access Control",
            caption_normalized="access control",
            pages=[10, 11],
            section_path=["Security", "Access Control"],
            column_signature="requirement|test_procedure|result",
            structure_hash="abc123",
            content_hash="def456",
            is_split=True,
        )

        assert identity.table_uid == "tbl_access_control_001"
        assert identity.pages == [10, 11]
        assert identity.is_split
        assert len(identity.section_path) == 2

    def test_identity_validation(self) -> None:
        """Test that identity validation rejects empty values."""
        with pytest.raises(ValueError, match="table_uid"):
            TableIdentity(
                table_uid="",
                caption_original="Test",
                caption_normalized="test",
                pages=[1],
                section_path=[],
                column_signature="",
                structure_hash="",
                content_hash="",
            )

        with pytest.raises(ValueError, match="pages"):
            TableIdentity(
                table_uid="tbl_test",
                caption_original="Test",
                caption_normalized="test",
                pages=[],
                section_path=[],
                column_signature="",
                structure_hash="",
                content_hash="",
            )


class TestTableIdentityResolver:
    """Test the table identity resolver."""

    def test_resolver_initialization(self) -> None:
        """Test resolver initialization."""
        resolver = TableIdentityResolver(
            caption_similarity_threshold=0.75,
            structure_match_threshold=0.9,
        )

        assert resolver.caption_similarity_threshold == 0.75
        assert resolver.structure_match_threshold == 0.9

    def test_normalize_caption(self) -> None:
        """Test caption normalization."""
        resolver = TableIdentityResolver()

        # Test continuation marker removal
        caption1 = "Table 5: Access Control (continued)"
        normalized1 = resolver._normalize_caption(caption1)
        assert "continued" not in normalized1.lower()

        # Test multiple spaces
        caption2 = "Table  5:   Access    Control"
        normalized2 = resolver._normalize_caption(caption2)
        assert "  " not in normalized2

    def test_generate_table_uid(self) -> None:
        """Test stable UID generation."""
        resolver = TableIdentityResolver()

        candidate = TableCandidate(
            backend_name="pdfplumber",
            page_number=10,
            bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
            cells=[],
            headers=["Col1", "Col2"],
            num_rows=5,
            num_cols=2,
            confidence=0.8,
            metadata={"caption_original": "Table 5: Access Control"},
        )

        uid = resolver._generate_table_uid(candidate, 0)

        # Should be deterministic
        assert uid == resolver._generate_table_uid(candidate, 0)

        # Should not contain invalid chars
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in uid)

    def test_jaccard_similarity(self) -> None:
        """Test Jaccard similarity calculation."""
        resolver = TableIdentityResolver()

        # Identical lists
        sim1 = resolver._jaccard_similarity(
            ["requirement", "procedure"],
            ["requirement", "procedure"],
        )
        assert sim1 == 1.0

        # Disjoint lists
        sim2 = resolver._jaccard_similarity(
            ["requirement"],
            ["result"],
        )
        assert sim2 == 0.0

        # Partial overlap
        sim3 = resolver._jaccard_similarity(
            ["requirement", "procedure", "result"],
            ["requirement", "note"],
        )
        assert 0 < sim3 < 1

    def test_compute_structure_hash(self) -> None:
        """Test structure hash computation."""
        resolver = TableIdentityResolver()

        candidate1 = TableCandidate(
            backend_name="pdfplumber",
            page_number=1,
            bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
            cells=[],
            headers=["Col1", "Col2"],
            num_rows=5,
            num_cols=2,
            confidence=0.8,
        )

        candidate2 = TableCandidate(
            backend_name="gmft",
            page_number=2,
            bbox={"x0": 10, "y0": 10, "x1": 110, "y1": 110},
            cells=[],
            headers=["Col1", "Col2"],
            num_rows=5,
            num_cols=2,
            confidence=0.7,
        )

        # Same structure should produce same hash
        hash1 = resolver._compute_structure_hash(candidate1)
        hash2 = resolver._compute_structure_hash(candidate2)
        assert hash1 == hash2

        # Different structure should produce different hash
        candidate3 = TableCandidate(
            backend_name="pdfplumber",
            page_number=1,
            bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
            cells=[],
            headers=["Col1", "Col2", "Col3"],
            num_rows=10,
            num_cols=3,
            confidence=0.8,
        )

        hash3 = resolver._compute_structure_hash(candidate3)
        assert hash1 != hash3

    def test_are_candidates_similar(self) -> None:
        """Test candidate similarity check."""
        resolver = TableIdentityResolver()

        # Same structure, consecutive pages
        cand1 = TableCandidate(
            backend_name="pdfplumber",
            page_number=10,
            bbox={"x0": 50, "y0": 100, "x1": 550, "y1": 300},
            cells=[],
            headers=["Col1", "Col2"],
            num_rows=5,
            num_cols=2,
            confidence=0.8,
        )

        cand2 = TableCandidate(
            backend_name="gmft",
            page_number=11,
            bbox={"x0": 50, "y0": 100, "x1": 550, "y1": 300},
            cells=[],
            headers=["Col1", "Col2"],
            num_rows=5,
            num_cols=2,
            confidence=0.7,
        )

        assert resolver._are_candidates_similar(cand1, cand2)

        # Different column count
        cand3 = TableCandidate(
            backend_name="pdfplumber",
            page_number=11,
            bbox={"x0": 50, "y0": 100, "x1": 550, "y1": 300},
            cells=[],
            headers=["Col1", "Col2", "Col3"],
            num_rows=5,
            num_cols=3,
            confidence=0.8,
        )

        assert not resolver._are_candidates_similar(cand1, cand3)

        # Far apart pages
        cand4 = TableCandidate(
            backend_name="pdfplumber",
            page_number=20,
            bbox={"x0": 50, "y0": 100, "x1": 550, "y1": 300},
            cells=[],
            headers=["Col1", "Col2"],
            num_rows=5,
            num_cols=2,
            confidence=0.8,
        )

        assert not resolver._are_candidates_similar(cand1, cand4)

    def test_group_candidates_by_similarity(self) -> None:
        """Test grouping of similar candidates."""
        resolver = TableIdentityResolver()

        candidates = [
            TableCandidate(
                backend_name="pdfplumber",
                page_number=10,
                bbox={"x0": 50, "y0": 100, "x1": 550, "y1": 300},
                cells=[],
                headers=["Req", "Proc"],
                num_rows=5,
                num_cols=2,
                confidence=0.8,
            ),
            TableCandidate(
                backend_name="gmft",
                page_number=11,
                bbox={"x0": 50, "y0": 100, "x1": 550, "y1": 300},
                cells=[],
                headers=["Req", "Proc"],
                num_rows=5,
                num_cols=2,
                confidence=0.7,
            ),
            TableCandidate(
                backend_name="camelot",
                page_number=12,
                bbox={"x0": 100, "y0": 200, "x1": 600, "y1": 400},
                cells=[],
                headers=["Other1", "Other2"],
                num_rows=3,
                num_cols=2,
                confidence=0.6,
            ),
        ]

        groups = resolver._group_candidates_by_similarity(candidates)

        # First two should be grouped (same table, consecutive pages)
        # Third is different table
        assert len(groups) == 2
        assert len(groups[0]) == 2
        assert len(groups[1]) == 1

    def test_is_split_continuation(self) -> None:
        """Test split table detection."""
        resolver = TableIdentityResolver()

        # Single candidate - not split
        group1 = [
            TableCandidate(
                backend_name="pdfplumber",
                page_number=10,
                bbox={"x0": 50, "y0": 100, "x1": 550, "y1": 300},
                cells=[],
                headers=["Col1", "Col2"],
                num_rows=5,
                num_cols=2,
            )
        ]
        assert not resolver._is_split_continuation(group1)

        # Multiple candidates on consecutive pages - is split
        group2 = [
            TableCandidate(
                backend_name="pdfplumber",
                page_number=10,
                bbox={"x0": 50, "y0": 100, "x1": 550, "y1": 300},
                cells=[],
                headers=["Col1", "Col2"],
                num_rows=5,
                num_cols=2,
            ),
            TableCandidate(
                backend_name="gmft",
                page_number=11,
                bbox={"x0": 50, "y0": 100, "x1": 550, "y1": 300},
                cells=[],
                headers=["Col1", "Col2"],
                num_rows=5,
                num_cols=2,
            ),
        ]
        assert resolver._is_split_continuation(group2)

    def test_resolve_tables_empty(self) -> None:
        """Test resolving empty candidate list."""
        resolver = TableIdentityResolver()

        result = resolver.resolve_tables([])
        assert result == {}

    def test_resolve_tables_single(self) -> None:
        """Test resolving a single table."""
        resolver = TableIdentityResolver()

        candidates = [
            TableCandidate(
                backend_name="pdfplumber",
                page_number=10,
                bbox={"x0": 50, "y0": 100, "x1": 550, "y1": 300},
                cells=[],
                headers=["Requirement", "Procedure"],
                num_rows=5,
                num_cols=2,
                confidence=0.8,
                metadata={"caption_original": "Table 5: Security Requirements"},
            )
        ]

        result = resolver.resolve_tables(candidates)

        assert len(result) == 1
        identity = list(result.values())[0]
        assert not identity.is_split
        assert identity.pages == [10]
        assert "security" in identity.caption_normalized.lower()
