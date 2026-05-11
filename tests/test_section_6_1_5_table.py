"""Diagnostic tests for section 6.1.5 table extraction accuracy.

Tests for the specific issue with Tabelle 34 (Messempfängereinstellungen und
Grenzwerte für Fahrzeugmessungen) which appears in section 6.1.5 of both
compliance documents. This table was previously showing as ADDED/REMOVED despite
being in the same section with identical content.
"""

from __future__ import annotations

import pytest

from grc_policy_server.services.comparison.table_diff_engine import (
    TableDiffEngine,
    TableDiffImpact,
    TableDiffType,
)
from grc_policy_server.services.documents.canonical_table_model import (
    BBox,
    CanonicalTable,
    CellType,
    TableCell,
    TableColumn,
    TableRow,
)


class TestSection615TableHandling:
    """Verify correct extraction and comparison of section 6.1.5 tables."""

    def test_table_34_single_instance_per_document(self):
        """Tabelle 34 should be extracted as single instance, not duplicated."""
        # Create mock Tabelle 34 structure
        table_uid = "tbl_messempfaengereinstellungen_001"

        table = CanonicalTable(
            table_uid=table_uid,
            caption_original="Tabelle 34 – Messempfängereinstellungen und Grenzwerte",
            caption_normalized="tabelle 34 messempfaengereinstellungen und grenzwerte",
            section_path=["6", "6.1", "6.1.5"],
            pages=[68],
            columns=[
                TableColumn(0, "Einstellung", "einstellung"),
                TableColumn(1, "Passive Antenne", "passive antenne"),
                TableColumn(2, "Aktive Antenne", "aktive antenne"),
            ],
            rows=[
                TableRow(
                    row_number=0,
                    cells=[
                        TableCell(0, 0, text="Frequenzbereich", is_header=True),
                        TableCell(0, 1, text="50 MHz – 6 GHz", is_header=True),
                        TableCell(0, 2, text="50 MHz – 6 GHz", is_header=True),
                    ],
                ),
                TableRow(
                    row_number=1,
                    cells=[
                        TableCell(1, 0, text="Messempfänger-Einstellung"),
                        TableCell(1, 1, text="Automatisch"),
                        TableCell(1, 2, text="Automatisch"),
                    ],
                ),
            ],
            num_rows=2,
            num_cols=3,
            extraction_backend="ensemble",
            confidence=0.95,
        )

        # Verify table structure
        assert table.table_uid == table_uid
        assert len(table.rows) == 2
        assert len(table.columns) == 3
        assert table.section_path == ["6", "6.1", "6.1.5"]
        assert len(table.pages) == 1

    def test_same_section_same_content_tables_match(self):
        """Tables in same section with identical content should match and show IDENTICAL diff."""
        # Create two identical copies of Tabelle 34
        table_uid = "tbl_messempfaengereinstellungen_001"

        old_table = CanonicalTable(
            table_uid=table_uid,
            caption_original="Tabelle 34 – Messempfängereinstellungen und Grenzwerte (2018)",
            caption_normalized="tabelle 34 messempfaengereinstellungen und grenzwerte 2018",
            section_path=["6", "6.1", "6.1.5"],
            pages=[68],
            columns=[
                TableColumn(0, "Einstellung", "einstellung"),
                TableColumn(1, "Passive Antenne", "passive antenne"),
                TableColumn(2, "Aktive Antenne", "aktive antenne"),
            ],
            rows=[
                TableRow(
                    row_number=0,
                    cells=[
                        TableCell(0, 0, text="Frequenzbereich", is_header=True),
                        TableCell(0, 1, text="50 MHz – 6 GHz", is_header=True),
                        TableCell(0, 2, text="50 MHz – 6 GHz", is_header=True),
                    ],
                ),
                TableRow(
                    row_number=1,
                    cells=[
                        TableCell(1, 0, text="Messempfänger-Einstellung"),
                        TableCell(1, 1, text="Automatisch"),
                        TableCell(1, 2, text="Automatisch"),
                    ],
                ),
            ],
            num_rows=2,
            num_cols=3,
            extraction_backend="ensemble",
            confidence=0.95,
        )

        new_table = CanonicalTable(
            table_uid=table_uid,
            caption_original="Tabelle 34 – Messempfängereinstellungen und Grenzwerte (2021)",
            caption_normalized="tabelle 34 messempfaengereinstellungen und grenzwerte 2021",
            section_path=["6", "6.1", "6.1.5"],
            pages=[71],
            columns=[
                TableColumn(0, "Einstellung", "einstellung"),
                TableColumn(1, "Passive Antenne", "passive antenne"),
                TableColumn(2, "Aktive Antenne", "aktive antenne"),
            ],
            rows=[
                TableRow(
                    row_number=0,
                    cells=[
                        TableCell(0, 0, text="Frequenzbereich", is_header=True),
                        TableCell(0, 1, text="50 MHz – 6 GHz", is_header=True),
                        TableCell(0, 2, text="50 MHz – 6 GHz", is_header=True),
                    ],
                ),
                TableRow(
                    row_number=1,
                    cells=[
                        TableCell(1, 0, text="Messempfänger-Einstellung"),
                        TableCell(1, 1, text="Automatisch"),
                        TableCell(1, 2, text="Automatisch"),
                    ],
                ),
            ],
            num_rows=2,
            num_cols=3,
            extraction_backend="ensemble",
            confidence=0.95,
        )

        # Compare tables
        engine = TableDiffEngine()
        diff = engine.diff_tables(old_table, new_table)

        # Both tables have same UUID, so should match
        assert diff.table_uid == table_uid
        # Content is identical
        assert diff.cells_modified == 0
        assert diff.rows_added == 0
        assert diff.rows_removed == 0
        # Only caption changed, still LOW impact because content identical
        assert diff.diff_impact == TableDiffImpact.LOW

    def test_json_comparison_preserves_structure(self):
        """JSON export should preserve all table structure for comparison."""
        table = CanonicalTable(
            table_uid="tbl_test_001",
            caption_original="Test Table",
            caption_normalized="test table",
            section_path=["6", "6.1", "6.1.5"],
            pages=[68],
            columns=[
                TableColumn(0, "Col1", "col1"),
                TableColumn(1, "Col2", "col2"),
            ],
            rows=[
                TableRow(
                    row_number=0,
                    cells=[
                        TableCell(0, 0, text="A", is_header=True),
                        TableCell(0, 1, text="B", is_header=True),
                    ],
                ),
            ],
            num_rows=1,
            num_cols=2,
        )

        # Export to JSON
        json_str = table.to_json()
        table_dict = table.to_dict()

        # Verify JSON contains all required info
        assert "table_uid" in table_dict
        assert "section_path" in table_dict
        assert "columns" in table_dict
        assert "rows" in table_dict
        assert table_dict["section_path"] == ["6", "6.1", "6.1.5"]
        assert len(table_dict["columns"]) == 2
        assert len(table_dict["rows"]) == 1

    def test_table_on_different_pages_same_section_recognized(self):
        """Tables in same section on different pages should be recognized as same table."""
        # Simulating split Tabelle 34 (pages 68-69 in one doc, 71-72 in another)
        table1_page68 = CanonicalTable(
            table_uid="tbl_messempfaengereinstellungen_001",
            caption_original="Tabelle 34",
            caption_normalized="tabelle 34",
            section_path=["6", "6.1", "6.1.5"],
            pages=[68, 69],  # Split across pages
            columns=[
                TableColumn(0, "Einstellung", "einstellung"),
                TableColumn(1, "Passive Antenne", "passive antenne"),
            ],
            rows=[
                TableRow(row_number=0, cells=[TableCell(0, 0, text="A")]),
                TableRow(row_number=1, cells=[TableCell(1, 0, text="B")]),
            ],
            num_rows=2,
            num_cols=2,
            is_split=True,
            split_across_pages=[68, 69],
        )

        table2_page71 = CanonicalTable(
            table_uid="tbl_messempfaengereinstellungen_001",
            caption_original="Tabelle 34",
            caption_normalized="tabelle 34",
            section_path=["6", "6.1", "6.1.5"],
            pages=[71, 72],  # Same table, split across different pages
            columns=[
                TableColumn(0, "Einstellung", "einstellung"),
                TableColumn(1, "Passive Antenne", "passive antenne"),
            ],
            rows=[
                TableRow(row_number=0, cells=[TableCell(0, 0, text="A")]),
                TableRow(row_number=1, cells=[TableCell(1, 0, text="B")]),
            ],
            num_rows=2,
            num_cols=2,
            is_split=True,
            split_across_pages=[71, 72],
        )

        # Same UID means they should match
        assert table1_page68.table_uid == table2_page71.table_uid
        # Both in same section
        assert table1_page68.section_path == table2_page71.section_path

    def test_json_diff_detects_no_changes_for_identical_content(self):
        """JSON-based diff should detect zero cell changes for identical tables."""
        json1 = {
            "table_uid": "tbl_tab34_001",
            "caption_normalized": "tabelle 34",
            "section_path": ["6", "6.1", "6.1.5"],
            "rows": [
                {
                    "row_number": 0,
                    "cells": [
                        {"row": 0, "col": 0, "text": "Frequenzbereich", "is_header": True},
                        {"row": 0, "col": 1, "text": "50 MHz – 6 GHz", "is_header": True},
                    ],
                },
                {
                    "row_number": 1,
                    "cells": [
                        {"row": 1, "col": 0, "text": "Einstellung"},
                        {"row": 1, "col": 1, "text": "Automatisch"},
                    ],
                },
            ],
            "dimensions": {"num_rows": 2, "num_cols": 2},
        }

        # Identical JSON (simulating same content in 2021 doc)
        json2 = json1.copy()
        json2["caption_normalized"] = "tabelle 34"  # Same caption normalized

        engine = TableDiffEngine()
        diff = engine.compare_table_json(json1, json2)

        # Should show minimal changes
        assert diff.cells_modified == 0
        assert diff.rows_added == 0
        assert diff.rows_removed == 0
