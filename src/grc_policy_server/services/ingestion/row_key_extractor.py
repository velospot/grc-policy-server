"""Row key extraction for compliance documents.

Extracts row identifiers (requirement IDs, test case IDs, conditions, steps)
to build composite keys that enable precise row-level change detection.

Examples:
- REQ-001:TC-02:normal_operation:step_1
- 5.2.1:case_a:edge_case:verification
- EMC-20:DIN-EN:condition_1:test_procedure
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from grc_policy_server.services.documents.canonical_table_model import CanonicalTable, TableCell
from grc_policy_server.services.ingestion.ontology.emc_ontology import (
    EMC_DOMAIN_ROW_KEYS,
    EMCTestClassifier,
    EMCTestType,
)


class RowKeyComponent(str, Enum):
    """Components of a compliance row key."""

    REQUIREMENT_ID = "requirement_id"
    TEST_CASE_ID = "test_case_id"
    CONDITION = "condition"
    PROCEDURE_STEP = "procedure_step"
    SUBSECTION = "subsection"


@dataclass
class RowKey:
    """Composite key for a compliance table row."""

    requirement_id: str = ""
    test_case_id: str = ""
    condition: str = ""
    procedure_step: str = ""

    def to_string(self) -> str:
        """Convert to colon-separated string format."""
        parts = [
            self.requirement_id,
            self.test_case_id,
            self.condition,
            self.procedure_step,
        ]
        # Remove trailing empty parts
        while parts and not parts[-1]:
            parts.pop()
        return ":".join(parts)

    @classmethod
    def from_string(cls, key_str: str) -> RowKey:
        """Parse from colon-separated string format."""
        parts = key_str.split(":")
        return cls(
            requirement_id=parts[0] if len(parts) > 0 else "",
            test_case_id=parts[1] if len(parts) > 1 else "",
            condition=parts[2] if len(parts) > 2 else "",
            procedure_step=parts[3] if len(parts) > 3 else "",
        )

    def is_empty(self) -> bool:
        """Check if key has no components."""
        return not any([self.requirement_id, self.test_case_id, self.condition, self.procedure_step])


class RowKeyExtractor:
    """Extract row keys from compliance table rows.

    When `domain_test_type` is set (or auto-detected from the table), domain-specific
    row keys are built using the EMC ontology column patterns (KB spec doc 15/24).
    For unknown test types the original generic extractor logic is used.
    """

    def __init__(
        self,
        requirement_patterns: list[str] | None = None,
        test_case_patterns: list[str] | None = None,
        condition_keywords: list[str] | None = None,
        step_keywords: list[str] | None = None,
        domain_test_type: EMCTestType = EMCTestType.UNKNOWN,
    ):
        """Initialize extractor with custom patterns.

        Args:
            requirement_patterns: Regex patterns for requirement IDs
            test_case_patterns: Regex patterns for test case IDs
            condition_keywords: Keywords indicating conditions
            step_keywords: Keywords indicating procedure steps
        """
        self.requirement_patterns = requirement_patterns or [
            r"(REQ|REF|EMV|EMC|DIN|IEC)-[\d]+",  # REQ-001, EMC-20, DIN-45678
            r"(\d+\.\d+(?:\.\d+)*)",  # Section numbers: 5.2.1, 3.4
            r"([A-Z]{2,}-\d+[A-Z]?)",  # Standard codes: EN-61000A
        ]

        self.test_case_patterns = test_case_patterns or [
            r"TC-[\d]+",  # Test Case: TC-01, TC-02
            r"(?:Test\s+)?Case\s+([A-Z]|\d+)",  # Case A, Case 1
            r"(?:case|variant|scenario)\s+(\w+)",  # case_a, variant_x
        ]

        self.condition_keywords = condition_keywords or [
            "normal operation",
            "edge case",
            "boundary condition",
            "extreme",
            "error case",
            "stress test",
            "steady state",
            "transient",
            "worst case",
            "best case",
        ]

        self.step_keywords = step_keywords or [
            "step",
            "procedure",
            "verification",
            "measurement",
            "check",
            "validation",
            "initialization",
            "execution",
            "conclusion",
        ]

        # Compile patterns
        self.compiled_requirement_patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in self.requirement_patterns
        ]
        self.compiled_test_case_patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in self.test_case_patterns
        ]

        self.domain_test_type = domain_test_type
        self._classifier = EMCTestClassifier()

    def classify_table_domain(self, table: CanonicalTable) -> EMCTestType:
        """Auto-detect EMC test type from table caption and column headers."""
        caption = table.caption_original or table.caption_normalized or ""
        headers = [col.name for col in table.columns]
        detected = self._classifier.classify_table(caption, headers)
        if detected == EMCTestType.UNKNOWN and table.section_path:
            detected = self._classifier.classify_from_section_path(table.section_path)
        return detected

    def extract_row_keys(self, table: CanonicalTable) -> dict[int, str]:
        """Extract row keys for all rows in a table.

        Uses domain-specific key structure when the test type is known,
        otherwise falls back to generic extraction.

        Args:
            table: Canonical table to extract keys from

        Returns:
            Mapping of row number to row key string
        """
        domain = self.domain_test_type
        if domain == EMCTestType.UNKNOWN:
            domain = self.classify_table_domain(table)

        row_keys: dict[int, str] = {}

        if domain != EMCTestType.UNKNOWN and domain in EMC_DOMAIN_ROW_KEYS:
            col_key_names = EMC_DOMAIN_ROW_KEYS[domain]
            col_index_map = self._map_columns_to_key_components(table, col_key_names)
            for row_idx, row in enumerate(table.rows):
                key_str = self._domain_row_key(row, col_index_map, col_key_names)
                if key_str:
                    row_keys[row_idx] = key_str
        else:
            for row_idx, row in enumerate(table.rows):
                key = self.extract_row_key(row, table)
                if key and not key.is_empty():
                    row_keys[row_idx] = key.to_string()

        return row_keys

    def _map_columns_to_key_components(
        self,
        table: CanonicalTable,
        key_component_names: list[str],
    ) -> dict[str, int]:
        """Map domain key component names to column indices using fuzzy header matching."""
        from grc_policy_server.services.ingestion.ontology.column_mapper import map_header
        from grc_policy_server.services.ingestion.ontology.emc_ontology import OntologyEntityType

        # Build a map from OntologyEntityType → column index
        entity_to_col: dict[str, int] = {}
        for col in table.columns:
            entity = map_header(col.name)
            if entity is not None:
                entity_to_col[entity.value.lower()] = col.index

        # Map key component names to column indices
        component_to_col: dict[str, int] = {}
        _COMPONENT_TO_ENTITY: dict[str, str] = {
            "phenomenon": OntologyEntityType.PHENOMENON.value.lower(),
            "frequency_range": OntologyEntityType.FREQUENCY_RANGE.value.lower(),
            "acceptance_criterion": OntologyEntityType.ACCEPTANCE_CRITERION.value.lower(),
            "limit_class": OntologyEntityType.EMISSION_LIMIT.value.lower(),
            "voltage_level": OntologyEntityType.FIELD_STRENGTH.value.lower(),
        }
        for name in key_component_names:
            entity_name = _COMPONENT_TO_ENTITY.get(name)
            if entity_name and entity_name in entity_to_col:
                component_to_col[name] = entity_to_col[entity_name]
            else:
                # Fallback: fuzzy column name match
                for col in table.columns:
                    if name.replace("_", " ") in col.name.lower() or col.name.lower() in name.replace("_", " "):
                        component_to_col[name] = col.index
                        break
        return component_to_col

    def _domain_row_key(
        self,
        row: Any,
        col_index_map: dict[str, int],
        key_component_names: list[str],
    ) -> str:
        """Build a domain row key from a row using the column index map."""
        cell_by_col: dict[int, str] = {cell.col: cell.text.strip() for cell in row.cells}
        parts: list[str] = []
        for name in key_component_names:
            col_idx = col_index_map.get(name)
            if col_idx is not None:
                text = cell_by_col.get(col_idx, "").lower().replace(" ", "_")[:40]
                parts.append(text)
        # Drop trailing empty parts
        while parts and not parts[-1]:
            parts.pop()
        return ":".join(parts)

    def extract_row_key(self, row: Any, table: CanonicalTable | None = None) -> RowKey:
        """Extract key from a single row.

        Args:
            row: TableRow object
            table: Parent table (for context)

        Returns:
            RowKey with extracted components
        """
        # Concatenate all cell texts in row
        row_text = " ".join(str(cell.text or "").strip() for cell in row.cells)

        key = RowKey()

        # Extract requirement ID
        key.requirement_id = self._extract_requirement_id(row_text)

        # Extract test case ID
        key.test_case_id = self._extract_test_case_id(row_text)

        # Extract condition
        key.condition = self._extract_condition(row_text)

        # Extract procedure step
        key.procedure_step = self._extract_procedure_step(row_text)

        return key

    def _extract_requirement_id(self, text: str) -> str:
        """Extract requirement ID from text."""
        for pattern in self.compiled_requirement_patterns:
            match = pattern.search(text)
            if match:
                return match.group(1) if match.lastindex else match.group(0)
        return ""

    def _extract_test_case_id(self, text: str) -> str:
        """Extract test case ID from text."""
        for pattern in self.compiled_test_case_patterns:
            match = pattern.search(text)
            if match:
                return match.group(1) if match.lastindex else match.group(0)
        return ""

    def _extract_condition(self, text: str) -> str:
        """Extract condition from text."""
        text_lower = text.lower()

        # Find first matching condition keyword
        for keyword in self.condition_keywords:
            if keyword in text_lower:
                # Normalize to underscores
                return keyword.lower().replace(" ", "_")

        return ""

    def _extract_procedure_step(self, text: str) -> str:
        """Extract procedure step from text."""
        text_lower = text.lower()

        # Look for step number or keyword
        step_match = re.search(r"(?:step|phase|stage|stage|part)\s+(\d+|[a-z])", text_lower)
        if step_match:
            return f"step_{step_match.group(1)}"

        # Look for procedure keywords
        for keyword in self.step_keywords:
            if keyword in text_lower:
                return keyword.lower().replace(" ", "_")

        return ""


class RowChangeDetector:
    """Detect types of row-level changes between tables."""

    def __init__(self, extractor: RowKeyExtractor | None = None):
        """Initialize detector.

        Args:
            extractor: RowKeyExtractor to use (creates default if not provided)
        """
        self.extractor = extractor or RowKeyExtractor()

    def detect_changes(
        self,
        old_table: CanonicalTable,
        new_table: CanonicalTable,
    ) -> dict[str, Any]:
        """Detect row-level changes between two versions of a table.

        Args:
            old_table: Previous version of table
            new_table: Current version of table

        Returns:
            Dictionary with change statistics and details
        """
        old_keys = self.extractor.extract_row_keys(old_table)
        new_keys = self.extractor.extract_row_keys(new_table)

        old_key_set = set(old_keys.values())
        new_key_set = set(new_keys.values())

        # Detect additions and removals
        added_keys = new_key_set - old_key_set
        removed_keys = old_key_set - new_key_set
        unchanged_keys = new_key_set & old_key_set

        # Build change report
        changes = {
            "total_old_rows": len(old_table.rows),
            "total_new_rows": len(new_table.rows),
            "rows_added": len(added_keys),
            "rows_removed": len(removed_keys),
            "rows_unchanged": len(unchanged_keys),
            "rows_modified": self._count_modified_rows(old_table, new_table, unchanged_keys),
            "added_row_keys": sorted(list(added_keys)),
            "removed_row_keys": sorted(list(removed_keys)),
            "column_changes": self._detect_column_changes(old_table, new_table),
        }

        return changes

    def _count_modified_rows(
        self,
        old_table: CanonicalTable,
        new_table: CanonicalTable,
        unchanged_keys: set[str],
    ) -> int:
        """Count rows with same key but different content."""
        if not unchanged_keys:
            return 0

        old_key_map = {k: i for i, k in enumerate(self.extractor.extract_row_keys(old_table).values())}
        new_key_map = {k: i for i, k in enumerate(self.extractor.extract_row_keys(new_table).values())}

        modified_count = 0
        for key in unchanged_keys:
            old_idx = old_key_map.get(key)
            new_idx = new_key_map.get(key)

            if old_idx is not None and new_idx is not None:
                old_row = old_table.rows[old_idx]
                new_row = new_table.rows[new_idx]

                # Compare row content
                old_text = " ".join(c.text for c in old_row.cells)
                new_text = " ".join(c.text for c in new_row.cells)

                if old_text.lower() != new_text.lower():
                    modified_count += 1

        return modified_count

    @staticmethod
    def _detect_column_changes(old_table: CanonicalTable, new_table: CanonicalTable) -> dict[str, Any]:
        """Detect column additions, removals, and renames.

        A RENAME is identified when a removed header and an added header both map
        to the same OntologyEntityType via map_header() — e.g. 'Prüf.Nr' and
        'Test.Nr' both map to TEST_NUMBER and are classified as COLUMN_RENAMED
        rather than separate COLUMN_REMOVED + COLUMN_ADDED events.
        """
        from grc_policy_server.services.ingestion.ontology.column_mapper import map_header

        old_cols = {col.name for col in old_table.columns}
        new_cols = {col.name for col in new_table.columns}
        raw_added = new_cols - old_cols
        raw_removed = old_cols - new_cols

        renames: list[dict[str, str]] = []
        matched_added: set[str] = set()
        matched_removed: set[str] = set()

        for removed_header in raw_removed:
            removed_entity = map_header(removed_header)
            if removed_entity is None:
                continue
            for added_header in raw_added:
                if added_header in matched_added:
                    continue
                added_entity = map_header(added_header)
                if added_entity is not None and added_entity == removed_entity:
                    renames.append({
                        "old_name": removed_header,
                        "new_name": added_header,
                        "entity_type": removed_entity.value,
                    })
                    matched_added.add(added_header)
                    matched_removed.add(removed_header)
                    break

        return {
            "columns_added": sorted(raw_added - matched_added),
            "columns_removed": sorted(raw_removed - matched_removed),
            "columns_renamed": renames,
            "column_count_old": len(old_table.columns),
            "column_count_new": len(new_table.columns),
        }
