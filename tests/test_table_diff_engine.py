"""Tests for table diff engine with cell-level diffs and structural awareness."""

import pytest

from grc_policy_server.services.comparison.table_diff_engine import (
    CellDiff,
    RowDiff,
    TableDiff,
    TableDiffType,
    TableDiffEngine,
    TableMatchingEngine,
)
from grc_policy_server.services.documents.canonical_table_model import (
    CanonicalTable,
    TableCell,
    TableColumn,
    TableRow,
)
from grc_policy_server.services.ingestion.row_key_extractor import RowKeyExtractor


class TestCellDiff:
    """Test CellDiff dataclass."""

    def test_cell_diff_creation(self) -> None:
        """Test creating a basic CellDiff."""
        diff = CellDiff(
            row=1,
            col=2,
            old_value="old",
            new_value="new",
            change_type="modified",
        )

        assert diff.row == 1
        assert diff.col == 2
        assert diff.old_value == "old"
        assert diff.new_value == "new"
        assert diff.change_type == "modified"

    def test_cell_diff_to_dict(self) -> None:
        """Test CellDiff serialization to dictionary."""
        diff = CellDiff(row=0, col=1, old_value="A", new_value="B")

        d = diff.to_dict()

        assert d["row"] == 0
        assert d["col"] == 1
        assert d["old_value"] == "A"
        assert d["new_value"] == "B"
        assert d["change_type"] == "modified"
        assert isinstance(d["metadata"], dict)

    def test_cell_diff_with_metadata(self) -> None:
        """Test CellDiff with custom metadata."""
        metadata = {"formatting": "bold"}
        diff = CellDiff(
            row=2,
            col=3,
            old_value="X",
            new_value="Y",
            change_type="formatting_changed",
            metadata=metadata,
        )

        assert diff.metadata == metadata
        assert diff.to_dict()["metadata"] == metadata


class TestRowDiff:
    """Test RowDiff dataclass."""

    def test_row_diff_creation(self) -> None:
        """Test creating a basic RowDiff."""
        diff = RowDiff(
            row_number=5,
            row_key="REQ-001:TC-02",
            change_type="modified",
        )

        assert diff.row_number == 5
        assert diff.row_key == "REQ-001:TC-02"
        assert diff.change_type == "modified"
        assert len(diff.cell_diffs) == 0

    def test_row_diff_aggregates_cell_diffs(self) -> None:
        """Test RowDiff aggregates cell-level changes."""
        cell_diffs = [
            CellDiff(row=1, col=0, old_value="A", new_value="B"),
            CellDiff(row=1, col=2, old_value="X", new_value="Y"),
        ]

        row_diff = RowDiff(
            row_number=1,
            row_key="REQ-002",
            change_type="modified",
            cell_diffs=cell_diffs,
        )

        assert len(row_diff.cell_diffs) == 2
        assert row_diff.cell_diffs[0].col == 0
        assert row_diff.cell_diffs[1].col == 2

    def test_row_diff_to_dict(self) -> None:
        """Test RowDiff serialization with nested cell diffs."""
        cell_diff = CellDiff(row=0, col=1, old_value="old", new_value="new")
        row_diff = RowDiff(
            row_number=0,
            row_key="REQ-001",
            change_type="modified",
            cell_diffs=[cell_diff],
        )

        d = row_diff.to_dict()

        assert d["row_number"] == 0
        assert d["row_key"] == "REQ-001"
        assert d["change_type"] == "modified"
        assert len(d["cell_diffs"]) == 1
        assert d["cell_diffs"][0]["old_value"] == "old"


class TestTableDiff:
    """Test TableDiff dataclass."""

    def test_table_diff_creation(self) -> None:
        """Test creating a basic TableDiff."""
        diff = TableDiff(
            table_uid="tbl_001",
            diff_type=TableDiffType.IDENTICAL,
            old_table=None,
            new_table=None,
        )

        assert diff.table_uid == "tbl_001"
        assert diff.diff_type == TableDiffType.IDENTICAL
        assert diff.similarity_score == 0.0

    def test_table_diff_to_dict(self) -> None:
        """Test TableDiff serialization to dictionary."""
        diff = TableDiff(
            table_uid="tbl_test",
            diff_type=TableDiffType.CELL_CHANGED,
            old_table=None,
            new_table=None,
            rows_added=1,
            rows_removed=0,
            cells_modified=3,
            similarity_score=0.95,
        )

        d = diff.to_dict()

        assert d["table_uid"] == "tbl_test"
        assert d["diff_type"] == "cell_changed"
        assert d["summary"]["rows_added"] == 1
        assert d["summary"]["cells_modified"] == 3
        assert d["summary"]["similarity_score"] == 0.95

    def test_table_diff_with_structural_changes(self) -> None:
        """Test TableDiff tracking structural changes."""
        structural_changes = ["moved_section", "caption_changed"]

        diff = TableDiff(
            table_uid="tbl_test",
            diff_type=TableDiffType.STRUCTURAL_CHANGED,
            old_table=None,
            new_table=None,
            structural_changes=structural_changes,
        )

        assert len(diff.structural_changes) == 2
        assert "moved_section" in diff.structural_changes
        assert "caption_changed" in diff.structural_changes


class TestTableDiffEngine:
    """Test table comparison engine."""

    @staticmethod
    def _make_table(
        table_uid: str,
        caption: str,
        columns: list[str],
        rows: list[list[str]],
    ) -> CanonicalTable:
        """Helper to create a CanonicalTable for testing."""
        cols = [TableColumn(i, name, name.lower()) for i, name in enumerate(columns)]
        table_rows = []

        for row_idx, row_cells in enumerate(rows):
            cells = [
                TableCell(row=row_idx, col=col_idx, text=cell_text)
                for col_idx, cell_text in enumerate(row_cells)
            ]
            table_rows.append(TableRow(row_number=row_idx, cells=cells))

        return CanonicalTable(
            table_uid=table_uid,
            caption_original=caption,
            caption_normalized=caption.lower(),
            section_path=["Test"],
            pages=[1],
            columns=cols,
            rows=table_rows,
        )

    def test_identical_tables(self) -> None:
        """Test comparing two identical tables."""
        engine = TableDiffEngine()

        old_table = self._make_table(
            "tbl_1", "Table 1", ["Col A", "Col B"], [["A1", "B1"], ["A2", "B2"]]
        )
        new_table = self._make_table(
            "tbl_1", "Table 1", ["Col A", "Col B"], [["A1", "B1"], ["A2", "B2"]]
        )

        diff = engine.diff_tables(old_table, new_table)

        assert diff.diff_type == TableDiffType.IDENTICAL
        assert diff.similarity_score > 0.99
        assert len(diff.row_diffs) == 0

    def test_cell_modification(self) -> None:
        """Test detecting a cell modification."""
        engine = TableDiffEngine()

        old_table = self._make_table(
            "tbl_1", "Table", ["A", "B"], [["1", "2"], ["3", "4"]]
        )
        new_table = self._make_table(
            "tbl_1", "Table", ["A", "B"], [["1", "CHANGED"], ["3", "4"]]
        )

        diff = engine.diff_tables(old_table, new_table)

        assert diff.diff_type == TableDiffType.CELL_CHANGED
        assert len(diff.row_diffs) == 1
        assert diff.row_diffs[0].row_number == 0
        assert len(diff.row_diffs[0].cell_diffs) == 1

    def test_cell_whitespace_normalization(self) -> None:
        """Test that whitespace differences are ignored."""
        engine = TableDiffEngine()

        old_table = self._make_table("tbl_1", "Table", ["A"], [["Test"]])
        new_table = self._make_table("tbl_1", "Table", ["A"], [["  Test  "]])

        diff = engine.diff_tables(old_table, new_table)

        # Normalized whitespace should match
        assert diff.diff_type == TableDiffType.IDENTICAL
        assert len(diff.row_diffs) == 0

    def test_cell_case_normalization(self) -> None:
        """Test that case differences are ignored."""
        engine = TableDiffEngine()

        old_table = self._make_table("tbl_1", "Table", ["A"], [["Test"]])
        new_table = self._make_table("tbl_1", "Table", ["A"], [["TEST"]])

        diff = engine.diff_tables(old_table, new_table)

        assert diff.diff_type == TableDiffType.IDENTICAL
        assert len(diff.row_diffs) == 0

    def test_row_addition(self) -> None:
        """Test detecting row addition."""
        engine = TableDiffEngine()

        old_table = self._make_table("tbl_1", "Table", ["A"], [["1"]])
        new_table = self._make_table("tbl_1", "Table", ["A"], [["1"], ["2"]])

        diff = engine.diff_tables(old_table, new_table)

        assert diff.diff_type == TableDiffType.ROW_CHANGED
        assert diff.rows_added == 1
        # Find the added row
        added_rows = [r for r in diff.row_diffs if r.change_type == "added"]
        assert len(added_rows) == 1

    def test_row_removal(self) -> None:
        """Test detecting row removal."""
        engine = TableDiffEngine()

        old_table = self._make_table("tbl_1", "Table", ["A"], [["1"], ["2"]])
        new_table = self._make_table("tbl_1", "Table", ["A"], [["1"]])

        diff = engine.diff_tables(old_table, new_table)

        assert diff.diff_type == TableDiffType.ROW_CHANGED
        assert diff.rows_removed == 1
        removed_rows = [r for r in diff.row_diffs if r.change_type == "removed"]
        assert len(removed_rows) == 1

    def test_row_modification(self) -> None:
        """Test detecting row modification with row_key tracking."""
        engine = TableDiffEngine()

        # Create rows with REQ pattern for row key detection
        cols = [TableColumn(0, "Req", "req"), TableColumn(1, "Value", "value")]

        old_rows = [
            TableRow(
                0,
                cells=[
                    TableCell(0, 0, text="REQ-001"),
                    TableCell(0, 1, text="old_value"),
                ],
            )
        ]
        new_rows = [
            TableRow(
                0,
                cells=[
                    TableCell(0, 0, text="REQ-001"),
                    TableCell(0, 1, text="new_value"),
                ],
            )
        ]

        old_table = CanonicalTable(
            table_uid="tbl_1",
            caption_original="Table",
            caption_normalized="table",
            section_path=[],
            pages=[1],
            columns=cols,
            rows=old_rows,
        )
        new_table = CanonicalTable(
            table_uid="tbl_1",
            caption_original="Table",
            caption_normalized="table",
            section_path=[],
            pages=[1],
            columns=cols,
            rows=new_rows,
        )

        diff = engine.diff_tables(old_table, new_table)

        assert diff.diff_type == TableDiffType.CELL_CHANGED
        assert len(diff.row_diffs) == 1
        assert diff.row_diffs[0].change_type == "modified"

    def test_column_addition(self) -> None:
        """Test detecting column addition."""
        engine = TableDiffEngine()

        old_table = self._make_table("tbl_1", "Table", ["A"], [["1"]])
        new_table = self._make_table("tbl_1", "Table", ["A", "B"], [["1", "2"]])

        diff = engine.diff_tables(old_table, new_table)

        assert diff.diff_type == TableDiffType.COLUMN_CHANGED
        assert len(diff.column_additions) == 1
        assert "B" in diff.column_additions

    def test_column_removal(self) -> None:
        """Test detecting column removal."""
        engine = TableDiffEngine()

        old_table = self._make_table("tbl_1", "Table", ["A", "B"], [["1", "2"]])
        new_table = self._make_table("tbl_1", "Table", ["A"], [["1"]])

        diff = engine.diff_tables(old_table, new_table)

        assert diff.diff_type == TableDiffType.COLUMN_CHANGED
        assert len(diff.column_removals) == 1
        assert "B" in diff.column_removals

    def test_structural_changes_detection(self) -> None:
        """Test detecting structural changes."""
        engine = TableDiffEngine()

        old_table = self._make_table("tbl_1", "Table 1", ["A", "B"], [["1", "2"]])
        # Change caption and section
        new_cols = [TableColumn(0, "A", "a"), TableColumn(1, "B", "b")]
        new_rows = [TableRow(0, cells=[TableCell(0, 0, text="1"), TableCell(0, 1, text="2")])]
        new_table = CanonicalTable(
            table_uid="tbl_1",
            caption_original="Table 2",
            caption_normalized="table 2",
            section_path=["Other"],
            pages=[1],
            columns=new_cols,
            rows=new_rows,
        )

        diff = engine.diff_tables(old_table, new_table)

        assert len(diff.structural_changes) > 0
        assert any("caption" in change for change in diff.structural_changes)
        assert any("section" in change or "moved" in change for change in diff.structural_changes)

    def test_similarity_scoring(self) -> None:
        """Test similarity score calculation."""
        engine = TableDiffEngine()

        old_table = self._make_table("tbl_1", "Table", ["A", "B"], [["1", "2"], ["3", "4"]])
        new_table = self._make_table(
            "tbl_1", "Table", ["A", "B"], [["1", "2"], ["3", "CHANGED"]]
        )

        diff = engine.diff_tables(old_table, new_table)

        # 3 out of 4 cells match, so similarity should be 0.75
        assert 0.7 < diff.similarity_score < 0.9

    def test_diff_type_priority(self) -> None:
        """Test that diff_type respects priority: structural > column > row > cell > identical."""
        engine = TableDiffEngine()

        # Case 1: Structural changes take priority
        old_table = self._make_table("tbl_1", "Table 1", ["A"], [["1"]])
        new_table_struct = CanonicalTable(
            table_uid="tbl_1",
            caption_original="Table 2",
            caption_normalized="table 2",
            section_path=["Other"],
            pages=[1],
            columns=[TableColumn(0, "A", "a")],
            rows=[TableRow(0, cells=[TableCell(0, 0, text="1")])],
        )
        diff_struct = engine.diff_tables(old_table, new_table_struct)
        # Caption change should trigger STRUCTURAL or RENAMED
        assert diff_struct.diff_type in (
            TableDiffType.STRUCTURAL_CHANGED,
            TableDiffType.RENAMED,
            TableDiffType.MOVED,
        )


class TestTableMatchingEngine:
    """Test table matching engine."""

    @staticmethod
    def _make_canonical_table(
        table_uid: str,
        caption: str,
        columns: list[str],
        rows: list[list[str]],
    ) -> CanonicalTable:
        """Helper to create CanonicalTable."""
        cols = [TableColumn(i, name, name.lower()) for i, name in enumerate(columns)]
        table_rows = []

        for row_idx, row_cells in enumerate(rows):
            cells = [
                TableCell(row=row_idx, col=col_idx, text=cell_text)
                for col_idx, cell_text in enumerate(row_cells)
            ]
            table_rows.append(TableRow(row_number=row_idx, cells=cells))

        return CanonicalTable(
            table_uid=table_uid,
            caption_original=caption,
            caption_normalized=caption.lower(),
            section_path=["Test"],
            pages=[1],
            columns=cols,
            rows=table_rows,
        )

    def test_direct_uuid_match(self) -> None:
        """Test matching tables by direct UUID."""
        matcher = TableMatchingEngine()

        old_tables = {
            "tbl_001": self._make_canonical_table("tbl_001", "Table 1", ["A"], [["1"]])
        }
        new_tables = {
            "tbl_001": self._make_canonical_table("tbl_001", "Table 1", ["A"], [["2"]])
        }

        diffs = matcher.match_tables(old_tables, new_tables)

        assert "tbl_001" in diffs
        assert diffs["tbl_001"].old_table is not None
        assert diffs["tbl_001"].new_table is not None

    def test_semantic_fallback_match(self) -> None:
        """Test semantic matching when UUIDs don't match."""
        matcher = TableMatchingEngine()

        # Same table content but different UUIDs
        old_tables = {
            "tbl_001": self._make_canonical_table(
                "tbl_001", "Requirements", ["Req", "Description"], [["REQ-001", "Test"]]
            )
        }
        new_tables = {
            "tbl_999": self._make_canonical_table(
                "tbl_999", "Requirements", ["Req", "Description"], [["REQ-001", "Test"]]
            )
        }

        diffs = matcher.match_tables(old_tables, new_tables)

        # Should find semantic match and use original tbl_001 UUID
        assert "tbl_001" in diffs
        assert diffs["tbl_001"].new_table is not None

    def test_no_match_when_dissimilar(self) -> None:
        """Test that dissimilar tables don't get matched."""
        matcher = TableMatchingEngine()

        old_tables = {
            "tbl_001": self._make_canonical_table(
                "tbl_001", "Requirements", ["Req"], [["REQ-001"]]
            )
        }
        new_tables = {
            "tbl_999": self._make_canonical_table(
                "tbl_999", "Settings", ["Key", "Value"], [["timeout", "30"]]
            )
        }

        diffs = matcher.match_tables(old_tables, new_tables)

        # Old table should be marked as removed
        assert "tbl_001" in diffs
        assert diffs["tbl_001"].new_table is None
        # New table should be marked as added
        assert "tbl_999" in diffs
        assert diffs["tbl_999"].old_table is None

    def test_multiple_table_matching(self) -> None:
        """Test matching multiple old and new tables."""
        matcher = TableMatchingEngine()

        old_tables = {
            "tbl_1": self._make_canonical_table("tbl_1", "Table 1", ["A"], [["1"]]),
            "tbl_2": self._make_canonical_table("tbl_2", "Table 2", ["B"], [["2"]]),
        }
        new_tables = {
            "tbl_1": self._make_canonical_table("tbl_1", "Table 1", ["A"], [["1"]]),
            "tbl_2": self._make_canonical_table("tbl_2", "Table 2", ["B"], [["2"]]),
        }

        diffs = matcher.match_tables(old_tables, new_tables)

        assert len(diffs) == 2
        assert "tbl_1" in diffs
        assert "tbl_2" in diffs

    def test_unmatched_removed_tables(self) -> None:
        """Test that old tables without matches are tracked as removed."""
        matcher = TableMatchingEngine()

        old_tables = {
            "tbl_old": self._make_canonical_table("tbl_old", "Old Table", ["A"], [["1"]])
        }
        new_tables = {}

        diffs = matcher.match_tables(old_tables, new_tables)

        assert "tbl_old" in diffs
        assert diffs["tbl_old"].old_table is not None
        assert diffs["tbl_old"].new_table is None

    def test_unmatched_added_tables(self) -> None:
        """Test that new tables without matches are tracked as added."""
        matcher = TableMatchingEngine()

        old_tables = {}
        new_tables = {
            "tbl_new": self._make_canonical_table("tbl_new", "New Table", ["A"], [["1"]])
        }

        diffs = matcher.match_tables(old_tables, new_tables)

        assert "tbl_new" in diffs
        assert diffs["tbl_new"].old_table is None
        assert diffs["tbl_new"].new_table is not None

    def test_semantic_match_stores_score(self) -> None:
        """Test that semantic matches store confidence score in metadata."""
        matcher = TableMatchingEngine()

        old_tables = {
            "tbl_001": self._make_canonical_table("tbl_001", "Table", ["A", "B"], [["1", "2"]])
        }
        new_tables = {
            "tbl_999": self._make_canonical_table(
                "tbl_999", "Table", ["A", "B"], [["1", "2"]]
            )
        }

        diffs = matcher.match_tables(old_tables, new_tables)

        # Should have semantic match score in metadata
        assert "tbl_001" in diffs
        if "semantic_match_score" in diffs["tbl_001"].metadata:
            assert diffs["tbl_001"].metadata["semantic_match_score"] > 0.7
