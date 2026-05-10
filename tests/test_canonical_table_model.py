"""Tests for canonical table model with cell graph and nested table support."""

import pytest

from grc_policy_server.services.documents.canonical_table_model import (
    BBox,
    CanonicalTable,
    CellType,
    TableCell,
    TableColumn,
    TableRow,
)


class TestBBox:
    """Test bounding box implementation."""

    def test_bbox_creation(self) -> None:
        """Test creating a bounding box."""
        bbox = BBox(x0=10, y0=20, x1=110, y1=120, page=1)

        assert bbox.x0 == 10
        assert bbox.page == 1
        assert bbox.width == 100
        assert bbox.height == 100
        assert bbox.area == 10000

    def test_bbox_to_dict(self) -> None:
        """Test bbox serialization."""
        bbox = BBox(x0=0, y0=0, x1=100, y1=100, page=5)
        d = bbox.to_dict()

        assert d["x0"] == 0
        assert d["page"] == 5
        assert "width" not in d  # Not included in dict


class TestTableColumn:
    """Test table column metadata."""

    def test_column_creation(self) -> None:
        """Test creating a column."""
        col = TableColumn(
            index=0,
            name="Requirement",
            normalized="requirement",
            width=150.0,
            data_type="text",
        )

        assert col.index == 0
        assert col.name == "Requirement"
        assert col.width == 150.0

    def test_column_to_dict(self) -> None:
        """Test column serialization."""
        col = TableColumn(index=0, name="Test", normalized="test")
        d = col.to_dict()

        assert d["index"] == 0
        assert d["name"] == "Test"
        assert d["normalized"] == "test"


class TestTableCell:
    """Test table cell with formatting and nested content."""

    def test_cell_creation(self) -> None:
        """Test creating a basic cell."""
        cell = TableCell(row=0, col=0, text="Test Cell")

        assert cell.row == 0
        assert cell.col == 0
        assert cell.text == "Test Cell"
        assert cell.rowspan == 1
        assert cell.colspan == 1
        assert cell.cell_type == CellType.TEXT

    def test_cell_with_merged_cells(self) -> None:
        """Test cell with rowspan and colspan."""
        cell = TableCell(
            row=1,
            col=2,
            text="Merged",
            rowspan=2,
            colspan=3,
        )

        assert cell.rowspan == 2
        assert cell.colspan == 3

    def test_cell_with_formatting(self) -> None:
        """Test cell with formatting attributes."""
        cell = TableCell(
            row=0,
            col=0,
            text="Bold & Italic",
            bold=True,
            italic=True,
            background_color="#FFFF00",
        )

        assert cell.bold
        assert cell.italic
        assert cell.background_color == "#FFFF00"

    def test_cell_to_dict(self) -> None:
        """Test cell serialization."""
        bbox = BBox(0, 0, 100, 50, 1)
        cell = TableCell(
            row=0,
            col=1,
            text="Data",
            bbox=bbox,
            bold=True,
            italic=False,
        )

        d = cell.to_dict()

        assert d["row"] == 0
        assert d["col"] == 1
        assert d["text"] == "Data"
        assert d["bbox"]["page"] == 1
        assert d["formatting"]["bold"]


class TestTableRow:
    """Test table row."""

    def test_row_creation(self) -> None:
        """Test creating a row."""
        cell1 = TableCell(row=0, col=0, text="A")
        cell2 = TableCell(row=0, col=1, text="B")

        row = TableRow(row_number=0, cells=[cell1, cell2])

        assert row.row_number == 0
        assert len(row.cells) == 2

    def test_row_with_uid(self) -> None:
        """Test row with compliance UID."""
        row = TableRow(
            row_number=1,
            cells=[],
            row_uid="REQ-001:TC-02:normal:step_1",
        )

        assert row.row_uid == "REQ-001:TC-02:normal:step_1"


class TestCanonicalTable:
    """Test canonical table model."""

    def test_table_creation(self) -> None:
        """Test creating a canonical table."""
        cols = [
            TableColumn(0, "Name", "name"),
            TableColumn(1, "Value", "value"),
        ]

        cells = [
            TableCell(row=0, col=0, text="A"),
            TableCell(row=0, col=1, text="1"),
        ]
        row = TableRow(row_number=0, cells=cells)

        table = CanonicalTable(
            table_uid="tbl_test_001",
            caption_original="Table 1",
            caption_normalized="table 1",
            section_path=["Data"],
            pages=[1],
            columns=cols,
            rows=[row],
        )

        assert table.table_uid == "tbl_test_001"
        assert table.num_rows == 1
        assert table.num_cols == 2

    def test_table_validation(self) -> None:
        """Test that table validates required fields."""
        with pytest.raises(ValueError, match="table_uid"):
            CanonicalTable(
                table_uid="",
                caption_original="Test",
                caption_normalized="test",
                section_path=[],
                pages=[1],
                columns=[],
                rows=[],
            )

        with pytest.raises(ValueError, match="pages"):
            CanonicalTable(
                table_uid="tbl_test",
                caption_original="Test",
                caption_normalized="test",
                section_path=[],
                pages=[],
                columns=[],
                rows=[],
            )

    def test_get_cell(self) -> None:
        """Test getting cell by position."""
        cells = [
            [
                TableCell(row=0, col=0, text="A"),
                TableCell(row=0, col=1, text="B"),
            ],
            [
                TableCell(row=1, col=0, text="C"),
                TableCell(row=1, col=1, text="D"),
            ],
        ]

        rows = [
            TableRow(row_number=0, cells=cells[0]),
            TableRow(row_number=1, cells=cells[1]),
        ]

        table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=[TableColumn(0, "X", "x"), TableColumn(1, "Y", "y")],
            rows=rows,
        )

        # Get existing cell
        cell = table.get_cell(0, 0)
        assert cell is not None
        assert cell.text == "A"

        # Get out-of-bounds
        assert table.get_cell(5, 5) is None
        assert table.get_cell(-1, 0) is None

    def test_cell_grid(self) -> None:
        """Test cell grid generation for comparison."""
        cells = [
            TableCell(row=0, col=0, text="A"),
            TableCell(row=0, col=1, text="B"),
            TableCell(row=1, col=0, text="C"),
            TableCell(row=1, col=1, text="D", rowspan=2),  # Spans 2 rows
        ]

        rows = [
            TableRow(row_number=0, cells=[cells[0], cells[1]]),
            TableRow(row_number=1, cells=[cells[2], cells[3]]),
        ]

        table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=[TableColumn(0, "X", "x"), TableColumn(1, "Y", "y")],
            rows=rows,
        )

        grid = table.cell_grid()

        assert grid[(0, 0)] == "A"
        assert grid[(0, 1)] == "B"
        assert grid[(1, 0)] == "C"
        # Merged cell should appear in both rows
        assert grid[(1, 1)] == "D"

    def test_to_html(self) -> None:
        """Test HTML generation."""
        cols = [
            TableColumn(0, "Name", "name"),
            TableColumn(1, "Value", "value"),
        ]

        cells = [
            TableCell(row=0, col=0, text="Alice"),
            TableCell(row=0, col=1, text="100"),
        ]
        row = TableRow(row_number=0, cells=cells)

        table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=cols,
            rows=[row],
        )

        html = table.to_html()

        assert "<table" in html
        assert "Name" in html
        assert "Alice" in html
        assert "100" in html
        assert "<thead>" in html
        assert "<tbody>" in html

    def test_to_html_with_formatting(self) -> None:
        """Test HTML generation with cell formatting."""
        cols = [TableColumn(0, "Bold", "bold")]

        cells = [
            TableCell(
                row=0,
                col=0,
                text="Bold Text",
                bold=True,
                background_color="#FF0000",
            ),
        ]
        row = TableRow(row_number=0, cells=cells)

        table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=cols,
            rows=[row],
        )

        html = table.to_html()

        assert "font-weight: bold" in html
        assert "background-color: #FF0000" in html

    def test_to_html_with_merged_cells(self) -> None:
        """Test HTML generation with rowspan/colspan."""
        cols = [
            TableColumn(0, "A", "a"),
            TableColumn(1, "B", "b"),
        ]

        cells = [
            TableCell(row=0, col=0, text="Merged", rowspan=2, colspan=2),
        ]
        row = TableRow(row_number=0, cells=cells)

        table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=cols,
            rows=[row],
        )

        html = table.to_html()

        assert 'rowspan="2"' in html
        assert 'colspan="2"' in html

    def test_to_markdown(self) -> None:
        """Test Markdown generation."""
        cols = [
            TableColumn(0, "Name", "name"),
            TableColumn(1, "Value", "value"),
        ]

        cells = [
            TableCell(row=0, col=0, text="Alice"),
            TableCell(row=0, col=1, text="100"),
        ]
        row = TableRow(row_number=0, cells=cells)

        table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=cols,
            rows=[row],
        )

        md = table.to_markdown()

        assert "|" in md
        assert "Name" in md
        assert "Alice" in md
        assert "100" in md
        assert "---" in md  # Separator row

    def test_to_dict(self) -> None:
        """Test table serialization to dict."""
        cols = [TableColumn(0, "Test", "test")]
        cells = [TableCell(row=0, col=0, text="Data")]
        row = TableRow(row_number=0, cells=cells)

        table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test Table",
            caption_normalized="test table",
            section_path=["Section"],
            pages=[1, 2],
            columns=cols,
            rows=[row],
            is_split=True,
            confidence=0.95,
        )

        d = table.to_dict()

        assert d["table_uid"] == "tbl_test"
        assert d["pages"] == [1, 2]
        assert d["split_info"]["is_split"]
        assert d["source"]["confidence"] == 0.95
        assert len(d["columns"]) == 1
        assert len(d["rows"]) == 1

    def test_html_escaping(self) -> None:
        """Test HTML special character escaping."""
        cols = [TableColumn(0, "Code", "code")]
        cells = [
            TableCell(row=0, col=0, text='<script>alert("xss")</script>'),
        ]
        row = TableRow(row_number=0, cells=cells)

        table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=cols,
            rows=[row],
        )

        html = table.to_html()

        # Check that special characters are escaped
        assert "&lt;script&gt;" in html
        assert "&quot;" in html
        assert "<script>" not in html  # No unescaped script tag
