"""Tests for multi-backend table extraction ensemble."""

import pytest

from grc_policy_server.services.ingestion.table_extraction_ensemble import (
    TableCandidate,
    TableExtractorEnsemble,
)


class TestTableCandidate:
    """Test TableCandidate data class."""

    def test_candidate_creation(self) -> None:
        """Test creating a table candidate."""
        candidate = TableCandidate(
            backend_name="test",
            page_number=1,
            bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
            cells=[],
            headers=["Col1", "Col2"],
            num_rows=5,
            num_cols=2,
            confidence=0.9,
        )

        assert candidate.backend_name == "test"
        assert candidate.page_number == 1
        assert candidate.num_cols == 2
        assert candidate.num_rows == 5
        assert candidate.confidence == 0.9

    def test_column_signature(self) -> None:
        """Test column signature generation for header matching."""
        candidate = TableCandidate(
            backend_name="test",
            page_number=1,
            bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
            cells=[],
            headers=["Requirement", "Test Procedure", "Result"],
            num_rows=5,
            num_cols=3,
        )

        sig = candidate.column_signature()
        assert sig == "requirement|test procedure|result"

    def test_cell_grid(self) -> None:
        """Test cell grid generation for overlap detection."""
        candidate = TableCandidate(
            backend_name="test",
            page_number=1,
            bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
            cells=[
                {"row": 0, "col": 0, "text": "Header1"},
                {"row": 0, "col": 1, "text": "Header2"},
                {"row": 1, "col": 0, "text": "Value1"},
                {"row": 1, "col": 1, "text": "Value2"},
            ],
            headers=["Header1", "Header2"],
            num_rows=2,
            num_cols=2,
        )

        grid = candidate.cell_grid()
        assert grid[(0, 0)] == "Header1"
        assert grid[(0, 1)] == "Header2"
        assert grid[(1, 0)] == "Value1"
        assert grid[(1, 1)] == "Value2"


class TestTableExtractorEnsemble:
    """Test the ensemble orchestration and reconciliation logic."""

    def test_ensemble_initialization(self) -> None:
        """Test ensemble initialization with various backend configurations."""
        ensemble = TableExtractorEnsemble(
            use_gmft=True,
            use_surya=True,
            use_camelot=True,
            use_pdfplumber=True,
            use_img2table=False,
        )

        assert ensemble.use_gmft
        assert ensemble.use_surya
        assert not ensemble.use_img2table

    def test_bbox_overlap_calculation(self) -> None:
        """Test bounding box overlap (IoU) calculation."""
        ensemble = TableExtractorEnsemble()

        # Identical boxes
        bbox1 = {"x0": 0, "y0": 0, "x1": 100, "y1": 100}
        bbox2 = {"x0": 0, "y0": 0, "x1": 100, "y1": 100}
        assert ensemble._bbox_overlap(bbox1, bbox2) == 1.0

        # Non-overlapping boxes
        bbox3 = {"x0": 200, "y0": 200, "x1": 300, "y1": 300}
        assert ensemble._bbox_overlap(bbox1, bbox3) == 0.0

        # Partial overlap (25x25 intersection)
        bbox4 = {"x0": 75, "y0": 75, "x1": 175, "y1": 175}
        overlap = ensemble._bbox_overlap(bbox1, bbox4)
        assert 0.02 < overlap < 0.05  # ~3.2% IoU

    def test_select_best_candidate(self) -> None:
        """Test selection of best candidate from duplicates."""
        ensemble = TableExtractorEnsemble()

        candidates = [
            TableCandidate(
                backend_name="gmft",
                page_number=1,
                bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
                cells=[],
                headers=["column_1", "column_2"],
                num_rows=5,
                num_cols=2,
                confidence=0.7,
            ),
            TableCandidate(
                backend_name="pdfplumber",
                page_number=1,
                bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
                cells=[],
                headers=["Requirement", "Procedure"],
                num_rows=5,
                num_cols=2,
                confidence=0.8,
            ),
        ]

        best = ensemble._select_best_candidate(candidates)

        # Should select pdfplumber because it has better headers
        assert best.backend_name == "pdfplumber"
        assert best.headers == ["Requirement", "Procedure"]

    def test_reconcile_candidates_same_page(self) -> None:
        """Test reconciliation of duplicate tables on same page."""
        ensemble = TableExtractorEnsemble()

        candidates = [
            TableCandidate(
                backend_name="gmft",
                page_number=1,
                bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
                cells=[],
                headers=["Col1", "Col2"],
                num_rows=5,
                num_cols=2,
                confidence=0.7,
            ),
            TableCandidate(
                backend_name="camelot",
                page_number=1,
                bbox={"x0": 5, "y0": 5, "x1": 95, "y1": 95},  # Overlapping
                cells=[],
                headers=["Col1", "Col2"],
                num_rows=5,
                num_cols=2,
                confidence=0.8,
            ),
            TableCandidate(
                backend_name="pdfplumber",
                page_number=1,
                bbox={"x0": 200, "y0": 200, "x1": 300, "y1": 300},  # Different table
                cells=[],
                headers=["Other1", "Other2"],
                num_rows=3,
                num_cols=2,
                confidence=0.75,
            ),
        ]

        reconciled = ensemble._reconcile_candidates(candidates)

        # Should merge the first two (overlapping) and keep the third
        assert len(reconciled) == 2

        # Check that the best candidate from the overlapping pair was selected
        page1_tables = [c for c in reconciled if c.page_number == 1]
        assert len(page1_tables) == 2

    def test_reconcile_candidates_different_pages(self) -> None:
        """Test reconciliation preserves candidates on different pages."""
        ensemble = TableExtractorEnsemble()

        candidates = [
            TableCandidate(
                backend_name="gmft",
                page_number=1,
                bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
                cells=[],
                headers=["Col1", "Col2"],
                num_rows=5,
                num_cols=2,
                confidence=0.7,
            ),
            TableCandidate(
                backend_name="pdfplumber",
                page_number=2,
                bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
                cells=[],
                headers=["Col1", "Col2"],
                num_rows=5,
                num_cols=2,
                confidence=0.8,
            ),
        ]

        reconciled = ensemble._reconcile_candidates(candidates)

        # Should keep both (different pages)
        assert len(reconciled) == 2
        assert {c.page_number for c in reconciled} == {1, 2}

    def test_ensemble_disabled_backends(self) -> None:
        """Test that ensemble respects backend enable/disable flags."""
        ensemble = TableExtractorEnsemble(
            use_gmft=False,
            use_surya=False,
            use_camelot=False,
            use_img2table=False,
            use_pdfplumber=True,
        )

        # Verify flags are set correctly
        assert not ensemble.use_gmft
        assert not ensemble.use_surya
        assert not ensemble.use_camelot
        assert not ensemble.use_img2table
        assert ensemble.use_pdfplumber
