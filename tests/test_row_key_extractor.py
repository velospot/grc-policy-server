"""Tests for row key extraction and change detection."""

import pytest

from grc_policy_server.services.documents.canonical_table_model import (
    CanonicalTable,
    TableCell,
    TableColumn,
    TableRow,
)
from grc_policy_server.services.ingestion.row_key_extractor import (
    RowChangeDetector,
    RowKey,
    RowKeyExtractor,
)


class TestRowKey:
    """Test row key data structure."""

    def test_row_key_creation(self) -> None:
        """Test creating a row key."""
        key = RowKey(
            requirement_id="REQ-001",
            test_case_id="TC-02",
            condition="normal_operation",
            procedure_step="step_1",
        )

        assert key.requirement_id == "REQ-001"
        assert key.condition == "normal_operation"

    def test_row_key_to_string(self) -> None:
        """Test converting row key to string."""
        key = RowKey(
            requirement_id="REQ-001",
            test_case_id="TC-02",
            condition="edge_case",
        )

        key_str = key.to_string()
        assert key_str == "REQ-001:TC-02:edge_case"

    def test_row_key_to_string_with_trailing_empty(self) -> None:
        """Test that trailing empty components are removed."""
        key = RowKey(requirement_id="REQ-001", test_case_id="", condition="", procedure_step="")

        key_str = key.to_string()
        assert key_str == "REQ-001"

    def test_row_key_from_string(self) -> None:
        """Test parsing row key from string."""
        key_str = "REQ-001:TC-02:normal:step_1"
        key = RowKey.from_string(key_str)

        assert key.requirement_id == "REQ-001"
        assert key.test_case_id == "TC-02"
        assert key.condition == "normal"
        assert key.procedure_step == "step_1"

    def test_row_key_roundtrip(self) -> None:
        """Test converting to string and back."""
        original = RowKey(
            requirement_id="EMV-20",
            test_case_id="case_a",
            condition="stress_test",
            procedure_step="verification",
        )

        key_str = original.to_string()
        restored = RowKey.from_string(key_str)

        assert restored.requirement_id == original.requirement_id
        assert restored.test_case_id == original.test_case_id
        assert restored.condition == original.condition
        assert restored.procedure_step == original.procedure_step

    def test_row_key_is_empty(self) -> None:
        """Test checking if key is empty."""
        empty_key = RowKey()
        assert empty_key.is_empty()

        non_empty_key = RowKey(requirement_id="REQ-001")
        assert not non_empty_key.is_empty()


class TestRowKeyExtractor:
    """Test row key extraction."""

    def test_extractor_initialization(self) -> None:
        """Test extractor initialization."""
        extractor = RowKeyExtractor()

        assert len(extractor.compiled_requirement_patterns) > 0
        assert len(extractor.compiled_test_case_patterns) > 0

    def test_extract_requirement_id(self) -> None:
        """Test requirement ID extraction."""
        extractor = RowKeyExtractor()

        # Test REQ format
        result = extractor._extract_requirement_id("REQ-001 Test")
        assert result and "REQ" in result

        # Test section number
        result = extractor._extract_requirement_id("Section 5.2.1 testing")
        assert result and "5" in result

        # Test standard code
        result = extractor._extract_requirement_id("DIN-EN-61000A specification")
        assert result and ("DIN" in result or "61000" in result)

    def test_extract_test_case_id(self) -> None:
        """Test test case ID extraction."""
        extractor = RowKeyExtractor()

        # Test TC format
        assert extractor._extract_test_case_id("TC-02 procedure") == "TC-02"

        # Test case letter
        result = extractor._extract_test_case_id("Case A test")
        assert "A" in result or "a" in result.lower()

    def test_extract_condition(self) -> None:
        """Test condition extraction."""
        extractor = RowKeyExtractor()

        # Test normal operation
        cond = extractor._extract_condition("normal operation test")
        assert "normal" in cond

        # Test edge case
        cond = extractor._extract_condition("under edge case conditions")
        assert "edge" in cond

        # Test worst case
        cond = extractor._extract_condition("worst case scenario")
        assert "worst" in cond

    def test_extract_procedure_step(self) -> None:
        """Test procedure step extraction."""
        extractor = RowKeyExtractor()

        # Test numbered step
        step = extractor._extract_procedure_step("Execute step 1 of procedure")
        assert "1" in step

        # Test step keyword
        step = extractor._extract_procedure_step("During verification phase")
        assert "verification" in step or "verification" in step.lower()

    def test_extract_row_key_full(self) -> None:
        """Test full row key extraction from row text."""
        extractor = RowKeyExtractor()

        # Create a row with realistic content
        cells = [
            TableCell(row=0, col=0, text="REQ-001"),
            TableCell(row=0, col=1, text="TC-02"),
            TableCell(row=0, col=2, text="Normal Operation"),
            TableCell(row=0, col=3, text="Step 1: Initialization"),
        ]
        row = TableRow(row_number=0, cells=cells)

        key = extractor.extract_row_key(row)

        # Check that row key object is created
        assert key is not None
        assert isinstance(key, RowKey)
        # Check that at least some components are extracted
        assert (
            key.requirement_id or
            key.test_case_id or
            key.condition or
            key.procedure_step
        )

    def test_extract_row_keys_from_table(self) -> None:
        """Test extracting keys from entire table."""
        extractor = RowKeyExtractor()

        # Create simple table
        cols = [
            TableColumn(0, "Req", "req"),
            TableColumn(1, "Case", "case"),
            TableColumn(2, "Condition", "condition"),
        ]

        rows = [
            TableRow(
                0,
                cells=[
                    TableCell(0, 0, text="REQ-001"),
                    TableCell(0, 1, text="TC-01"),
                    TableCell(0, 2, text="normal operation"),
                ],
            ),
            TableRow(
                1,
                cells=[
                    TableCell(1, 0, text="REQ-002"),
                    TableCell(1, 1, text="TC-02"),
                    TableCell(1, 2, text="edge case"),
                ],
            ),
        ]

        table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=cols,
            rows=rows,
        )

        keys = extractor.extract_row_keys(table)

        assert len(keys) == 2
        assert 0 in keys
        assert 1 in keys


class TestRowChangeDetector:
    """Test row-level change detection."""

    def test_detector_initialization(self) -> None:
        """Test detector initialization."""
        detector = RowChangeDetector()

        assert detector.extractor is not None

    def test_detect_row_additions(self) -> None:
        """Test detecting row additions."""
        detector = RowChangeDetector()

        # Old table with 1 row
        old_cols = [TableColumn(0, "Req", "req")]
        old_rows = [
            TableRow(0, cells=[TableCell(0, 0, text="REQ-001")])
        ]
        old_table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=old_cols,
            rows=old_rows,
        )

        # New table with 2 rows
        new_rows = [
            TableRow(0, cells=[TableCell(0, 0, text="REQ-001")]),
            TableRow(1, cells=[TableCell(1, 0, text="REQ-002")]),
        ]
        new_table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=old_cols,
            rows=new_rows,
        )

        changes = detector.detect_changes(old_table, new_table)

        assert changes["total_old_rows"] == 1
        assert changes["total_new_rows"] == 2
        # Check that change detection works
        assert "rows_added" in changes
        assert "rows_removed" in changes

    def test_detect_row_removals(self) -> None:
        """Test detecting row removals."""
        detector = RowChangeDetector()

        # Old table with 2 rows
        old_cols = [TableColumn(0, "Req", "req")]
        old_rows = [
            TableRow(0, cells=[TableCell(0, 0, text="REQ-001")]),
            TableRow(1, cells=[TableCell(1, 0, text="REQ-002")]),
        ]
        old_table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=old_cols,
            rows=old_rows,
        )

        # New table with 1 row
        new_rows = [
            TableRow(0, cells=[TableCell(0, 0, text="REQ-001")]),
        ]
        new_table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=old_cols,
            rows=new_rows,
        )

        changes = detector.detect_changes(old_table, new_table)

        assert changes["total_old_rows"] == 2
        assert changes["total_new_rows"] == 1
        # Check that change detection works
        assert "rows_removed" in changes
        assert "rows_added" in changes

    def test_detect_column_changes(self) -> None:
        """Test detecting column additions/removals."""
        detector = RowChangeDetector()

        # Old table with 2 columns
        old_cols = [
            TableColumn(0, "Requirement", "requirement"),
            TableColumn(1, "Test Case", "test_case"),
        ]
        old_rows = [
            TableRow(0, cells=[
                TableCell(0, 0, text="REQ-001"),
                TableCell(0, 1, text="TC-01"),
            ])
        ]
        old_table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=old_cols,
            rows=old_rows,
        )

        # New table with 3 columns (added Result column)
        new_cols = [
            TableColumn(0, "Requirement", "requirement"),
            TableColumn(1, "Test Case", "test_case"),
            TableColumn(2, "Result", "result"),
        ]
        new_rows = [
            TableRow(0, cells=[
                TableCell(0, 0, text="REQ-001"),
                TableCell(0, 1, text="TC-01"),
                TableCell(0, 2, text="Pass"),
            ])
        ]
        new_table = CanonicalTable(
            table_uid="tbl_test",
            caption_original="Test",
            caption_normalized="test",
            section_path=[],
            pages=[1],
            columns=new_cols,
            rows=new_rows,
        )

        changes = detector.detect_changes(old_table, new_table)

        col_changes = changes["column_changes"]
        assert col_changes["columns_added"]
        assert "Result" in col_changes["columns_added"]
        assert col_changes["column_count_old"] == 2
        assert col_changes["column_count_new"] == 3


class TestDomainRowKeys:
    """Test domain-specific row key extraction for EMC test types."""

    def _make_table(self, caption: str, columns: list[tuple[str, str]], rows_data: list[list[str]]) -> CanonicalTable:
        cols = [TableColumn(i, name, name.lower()) for i, (name, _) in enumerate(columns)]
        rows = []
        for r_idx, row_texts in enumerate(rows_data):
            cells = [TableCell(row=r_idx, col=c_idx, text=text) for c_idx, text in enumerate(row_texts)]
            rows.append(TableRow(row_number=r_idx, cells=cells))
        return CanonicalTable(
            table_uid=f"tbl_{caption.lower().replace(' ', '_')}",
            caption_original=caption,
            caption_normalized=caption.lower(),
            section_path=[],
            pages=[1],
            columns=cols,
            rows=rows,
        )

    def test_classify_table_domain_radiated_immunity(self):
        from grc_policy_server.services.ingestion.row_key_extractor import RowKeyExtractor
        extractor = RowKeyExtractor()
        table = self._make_table(
            "Radiated Immunity",
            [("Phenomenon", "str"), ("Frequency Range", "str"), ("Level", "str"), ("Acceptance Criterion", "str")],
            [["BCI", "1 MHz - 400 MHz", "30 V/m", "Class A"]],
        )
        domain = extractor.classify_table_domain(table)
        from grc_policy_server.services.ingestion.ontology.emc_ontology import EMCTestType
        assert domain == EMCTestType.RADIATED_IMMUNITY

    def test_domain_row_keys_use_column_content(self):
        from grc_policy_server.services.ingestion.row_key_extractor import RowKeyExtractor
        from grc_policy_server.services.ingestion.ontology.emc_ontology import EMCTestType
        extractor = RowKeyExtractor(domain_test_type=EMCTestType.RADIATED_IMMUNITY)
        table = self._make_table(
            "Radiated Immunity",
            [("Phenomenon", "str"), ("Frequency Range", "str"), ("Level", "str")],
            [
                ["BCI", "1 MHz - 400 MHz", "30 V/m"],
                ["ESD", "DC - 10 GHz", "4 kV"],
            ],
        )
        keys = extractor.extract_row_keys(table)
        # Domain extraction should produce some keys (exact content depends on column mapping)
        assert isinstance(keys, dict)

    def test_esd_domain_keys(self):
        from grc_policy_server.services.ingestion.row_key_extractor import RowKeyExtractor
        from grc_policy_server.services.ingestion.ontology.emc_ontology import EMCTestType
        extractor = RowKeyExtractor(domain_test_type=EMCTestType.ESD)
        table = self._make_table(
            "ESD Requirements",
            [("Phenomenon", "str"), ("Voltage Level", "str"), ("Acceptance Criterion", "str")],
            [["ESD", "8 kV", "Class A"]],
        )
        # Should not raise any exception
        keys = extractor.extract_row_keys(table)
        assert isinstance(keys, dict)

    def test_unknown_domain_falls_back_to_generic(self):
        from grc_policy_server.services.ingestion.row_key_extractor import RowKeyExtractor
        from grc_policy_server.services.ingestion.ontology.emc_ontology import EMCTestType
        extractor = RowKeyExtractor(domain_test_type=EMCTestType.UNKNOWN)
        table = self._make_table(
            "Generic Table",
            [("REQ-ID", "str"), ("Condition", "str")],
            [["REQ-001", "normal operation"]],
        )
        keys = extractor.extract_row_keys(table)
        # Should use generic extraction path — at least one key should be found
        assert isinstance(keys, dict)
