"""Enhanced table comparison engine with cell-level diffs and structural awareness.

Integrates multi-backend extraction, identity resolution, canonical tables,
and row keys to provide granular table-level change detection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from grc_policy_server.services.documents.canonical_table_model import CanonicalTable
from grc_policy_server.services.ingestion.row_key_extractor import RowChangeDetector, RowKeyExtractor

logger = logging.getLogger(__name__)


class TableDiffType(str, Enum):
    """Type of table-level difference."""

    IDENTICAL = "identical"
    COLUMN_CHANGED = "column_changed"  # Columns added/removed/reordered
    ROW_CHANGED = "row_changed"  # Rows added/removed/modified
    CELL_CHANGED = "cell_changed"  # Cell content modified
    STRUCTURAL_CHANGED = "structural_changed"  # Split, moved, merged
    RENAMED = "renamed"  # Table number/caption changed
    MOVED = "moved"  # Section path changed


class TableDiffImpact(str, Enum):
    """Impact severity of table changes."""

    IDENTICAL = "identical"  # No change
    LOW = "low"  # Renumbering, section moves (content identical)
    MEDIUM = "medium"  # Row/column additions (structure changed)
    HIGH = "high"  # Cell content modified (data changed)


@dataclass(frozen=True)
class CellDiff:
    """Difference in a single cell."""

    row: int
    col: int
    old_value: str = ""
    new_value: str = ""
    change_type: str = "modified"  # modified, formatting_changed
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "col": self.col,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "change_type": self.change_type,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class RowDiff:
    """Difference in a table row."""

    row_number: int
    row_key: str = ""
    change_type: str = "modified"  # added, removed, modified
    cell_diffs: list[CellDiff] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "row_key": self.row_key,
            "change_type": self.change_type,
            "cell_diffs": [cd.to_dict() for cd in self.cell_diffs],
            "metadata": self.metadata,
        }


@dataclass
class TableDiff:
    """Complete difference between two versions of a table."""

    table_uid: str
    diff_type: TableDiffType
    old_table: CanonicalTable | None
    new_table: CanonicalTable | None
    # Changes
    row_diffs: list[RowDiff] = field(default_factory=list)
    column_additions: list[str] = field(default_factory=list)
    column_removals: list[str] = field(default_factory=list)
    # Metadata
    similarity_score: float = 0.0
    rows_added: int = 0
    rows_removed: int = 0
    rows_modified: int = 0
    cells_modified: int = 0
    structural_changes: list[str] = field(default_factory=list)
    diff_impact: TableDiffImpact = TableDiffImpact.IDENTICAL
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_uid": self.table_uid,
            "diff_type": self.diff_type.value,
            "impact": self.diff_impact.value,
            "row_diffs": [rd.to_dict() for rd in self.row_diffs],
            "column_changes": {
                "added": self.column_additions,
                "removed": self.column_removals,
            },
            "summary": {
                "similarity_score": self.similarity_score,
                "rows_added": self.rows_added,
                "rows_removed": self.rows_removed,
                "rows_modified": self.rows_modified,
                "cells_modified": self.cells_modified,
            },
            "structural_changes": self.structural_changes,
            "metadata": self.metadata,
        }


class TableDiffEngine:
    """Compute detailed diffs between table versions."""

    def __init__(
        self,
        row_key_extractor: RowKeyExtractor | None = None,
        cell_similarity_threshold: float = 0.85,
    ):
        """Initialize diff engine.

        Args:
            row_key_extractor: Extractor for compliance row keys
            cell_similarity_threshold: Threshold for considering cells identical
        """
        self.row_key_extractor = row_key_extractor or RowKeyExtractor()
        self.change_detector = RowChangeDetector(self.row_key_extractor)
        self.cell_similarity_threshold = cell_similarity_threshold

    def diff_tables(
        self,
        old_table: CanonicalTable,
        new_table: CanonicalTable,
    ) -> TableDiff:
        """Compute detailed diff between two table versions.

        Args:
            old_table: Previous version of table
            new_table: Current version of table

        Returns:
            TableDiff with granular change information
        """
        # Compute similarity score
        similarity = self._compute_table_similarity(old_table, new_table)

        # Detect structural changes
        structural_changes = self._detect_structural_changes(old_table, new_table)

        # Detect row-level changes
        row_changes = self.change_detector.detect_changes(old_table, new_table)

        # Build row diffs
        row_diffs = self._build_row_diffs(old_table, new_table, row_changes)

        # Detect column changes
        col_changes = row_changes.get("column_changes", {})
        column_additions = col_changes.get("columns_added", [])
        column_removals = col_changes.get("columns_removed", [])

        # Count actual row changes from row_diffs (more reliable than row_changes for non-compliance tables)
        rows_added = sum(1 for rd in row_diffs if rd.change_type == "added")
        rows_removed = sum(1 for rd in row_diffs if rd.change_type == "removed")
        rows_modified = sum(1 for rd in row_diffs if rd.change_type == "modified")

        # Determine diff type
        diff_type = self._determine_diff_type(
            similarity,
            structural_changes,
            column_additions,
            column_removals,
            row_changes,
            rows_added,
            rows_removed,
            rows_modified,
        )

        # Build final diff
        diff = TableDiff(
            table_uid=new_table.table_uid,
            diff_type=diff_type,
            old_table=old_table,
            new_table=new_table,
            row_diffs=row_diffs,
            column_additions=column_additions,
            column_removals=column_removals,
            similarity_score=similarity,
            rows_added=rows_added,
            rows_removed=rows_removed,
            rows_modified=rows_modified,
            cells_modified=sum(len(rd.cell_diffs) for rd in row_diffs),
            structural_changes=structural_changes,
            metadata={
                "old_caption": old_table.caption_original,
                "new_caption": new_table.caption_original,
                "old_pages": old_table.pages,
                "new_pages": new_table.pages,
            },
        )

        # Classify impact severity
        diff.diff_impact = self._classify_diff_impact(diff)

        return diff

    def _compute_table_similarity(self, old_table: CanonicalTable, new_table: CanonicalTable) -> float:
        """Compute similarity score between tables (0.0 to 1.0)."""
        if not old_table.rows or not new_table.rows:
            return 0.0

        # Content-based similarity using cell matching
        old_grid = old_table.cell_grid()
        new_grid = new_table.cell_grid()

        if not old_grid and not new_grid:
            return 1.0

        # Count matching cells
        matches = 0
        total_cells = max(len(old_grid), len(new_grid))

        for pos, old_text in old_grid.items():
            new_text = new_grid.get(pos, "")
            if self._cells_match(old_text, new_text):
                matches += 1

        # Penalize for structural differences
        structure_penalty = abs(len(old_table.columns) - len(new_table.columns)) / max(
            len(old_table.columns), len(new_table.columns), 1
        )

        similarity = (matches / total_cells if total_cells > 0 else 0) * (1 - structure_penalty * 0.5)

        return min(1.0, max(0.0, similarity))

    def _detect_structural_changes(self, old_table: CanonicalTable, new_table: CanonicalTable) -> list[str]:
        """Detect structural changes between tables."""
        changes = []

        # Detect table movement
        if old_table.section_path != new_table.section_path:
            changes.append("moved_section")

        # Detect split/merge
        if old_table.is_split != new_table.is_split:
            if new_table.is_split:
                changes.append("split_across_pages")
            else:
                changes.append("merged_from_split")

        # Detect caption change
        if old_table.caption_normalized != new_table.caption_normalized:
            changes.append("caption_changed")

        # Detect column reordering (if column count same but headers differ)
        if (
            len(old_table.columns) == len(new_table.columns)
            and old_table.columns != new_table.columns
        ):
            old_headers = [c.name for c in old_table.columns]
            new_headers = [c.name for c in new_table.columns]

            if set(old_headers) == set(new_headers):
                changes.append("columns_reordered")

        return changes

    def _build_row_diffs(
        self,
        old_table: CanonicalTable,
        new_table: CanonicalTable,
        row_changes: dict[str, Any],
    ) -> list[RowDiff]:
        """Build detailed row-level diffs."""
        row_diffs: list[RowDiff] = []

        old_row_keys = self.row_key_extractor.extract_row_keys(old_table)
        new_row_keys = self.row_key_extractor.extract_row_keys(new_table)

        # Map row keys to row indices
        old_key_map = {k: i for i, k in enumerate(old_row_keys.values())}
        new_key_map = {k: i for i, k in enumerate(new_row_keys.values())}

        # Track which old rows have been matched
        matched_old_indices = set()

        # Process unchanged and modified rows
        for new_idx, new_row in enumerate(new_table.rows):
            new_key = new_row_keys.get(new_idx, "")
            old_idx = None

            # Try row_key based matching first
            if new_key:
                old_idx = old_key_map.get(new_key)

            # Fall back to position-based matching if no row_key match
            if old_idx is None and new_idx < len(old_table.rows):
                old_idx = new_idx

            if old_idx is not None and old_idx < len(old_table.rows):
                # Row exists in both versions - check for modifications
                old_row = old_table.rows[old_idx]
                cell_diffs = self._compare_rows(old_row, new_row, old_table, new_table)
                matched_old_indices.add(old_idx)

                if cell_diffs:
                    row_diffs.append(
                        RowDiff(
                            row_number=new_idx,
                            row_key=new_key,
                            change_type="modified",
                            cell_diffs=cell_diffs,
                        )
                    )
            else:
                # Row is new
                row_diffs.append(
                    RowDiff(
                        row_number=new_idx,
                        row_key=new_key,
                        change_type="added",
                    )
                )

        # Process removed rows
        for old_idx, old_row in enumerate(old_table.rows):
            if old_idx in matched_old_indices:
                continue

            old_key = old_row_keys.get(old_idx, "")
            # Report as removed if it has a row_key or if it's beyond the new table length
            if old_key or old_idx >= len(new_table.rows):
                row_diffs.append(
                    RowDiff(
                        row_number=old_idx,
                        row_key=old_key,
                        change_type="removed",
                    )
                )

        return row_diffs

    def _compare_rows(
        self,
        old_row: Any,  # TableRow
        new_row: Any,  # TableRow
        old_table: CanonicalTable,
        new_table: CanonicalTable,
    ) -> list[CellDiff]:
        """Compare two rows cell-by-cell, including merged cell structure."""
        cell_diffs: list[CellDiff] = []

        for col_idx in range(max(len(old_row.cells), len(new_row.cells))):
            old_cell = next((c for c in old_row.cells if c.col == col_idx), None)
            new_cell = next((c for c in new_row.cells if c.col == col_idx), None)

            if old_cell and new_cell:
                # Both cells exist - compare text and structure
                text_changed = not self._cells_match(old_cell.text, new_cell.text)

                # Check for merged cell structure changes (rowspan/colspan)
                structure_changed = (
                    old_cell.rowspan != new_cell.rowspan
                    or old_cell.colspan != new_cell.colspan
                )

                if text_changed or structure_changed:
                    metadata = {}
                    if structure_changed:
                        metadata["rowspan_old"] = old_cell.rowspan
                        metadata["rowspan_new"] = new_cell.rowspan
                        metadata["colspan_old"] = old_cell.colspan
                        metadata["colspan_new"] = new_cell.colspan

                    change_type = (
                        "merged_cell_changed" if structure_changed else "modified"
                    )

                    cell_diffs.append(
                        CellDiff(
                            row=old_row.row_number,
                            col=col_idx,
                            old_value=old_cell.text,
                            new_value=new_cell.text,
                            change_type=change_type,
                            metadata=metadata,
                        )
                    )

                # Recursively compare nested tables if present
                if old_cell.children or new_cell.children:
                    nested_diffs = self._compare_nested_tables(
                        old_cell.children, new_cell.children
                    )
                    cell_diffs.extend(nested_diffs)

            elif new_cell and not old_cell:
                # Cell added (column added or cell content added)
                cell_diffs.append(
                    CellDiff(
                        row=new_row.row_number,
                        col=col_idx,
                        old_value="",
                        new_value=new_cell.text,
                        change_type="added",
                    )
                )
            elif old_cell and not new_cell:
                # Cell removed
                cell_diffs.append(
                    CellDiff(
                        row=old_row.row_number,
                        col=col_idx,
                        old_value=old_cell.text,
                        new_value="",
                        change_type="removed",
                    )
                )

        return cell_diffs

    def _compare_nested_tables(
        self,
        old_nested: list[CanonicalTable],
        new_nested: list[CanonicalTable],
    ) -> list[CellDiff]:
        """Recursively compare nested tables in cells."""
        nested_diffs: list[CellDiff] = []

        # Match nested tables by UID
        old_by_uid = {t.table_uid: t for t in old_nested}
        new_by_uid = {t.table_uid: t for t in new_nested}

        # Direct UID matches
        for uid, old_nested_table in old_by_uid.items():
            if uid in new_by_uid:
                new_nested_table = new_by_uid[uid]
                nested_table_diff = self.diff_tables(old_nested_table, new_nested_table)

                # Convert TableDiff to CellDiff representation
                if nested_table_diff.diff_type != TableDiffType.IDENTICAL:
                    nested_diffs.append(
                        CellDiff(
                            row=-1,  # Special marker for nested table diff
                            col=-1,
                            old_value=f"<nested table: {uid}>",
                            new_value=f"<nested table modified>",
                            change_type="nested_table_changed",
                            metadata={
                                "nested_table_uid": uid,
                                "nested_diff_type": nested_table_diff.diff_type.value,
                                "nested_impact": nested_table_diff.diff_impact.value,
                            },
                        )
                    )

        return nested_diffs

    def _determine_diff_type(
        self,
        similarity: float,
        structural_changes: list[str],
        column_additions: list[str],
        column_removals: list[str],
        row_changes: dict[str, Any],
        rows_added: int = 0,
        rows_removed: int = 0,
        rows_modified: int = 0,
    ) -> TableDiffType:
        """Determine the primary type of table change."""
        # No changes
        if similarity > 0.99 and not structural_changes:
            return TableDiffType.IDENTICAL

        # Structural changes take priority
        if structural_changes:
            if "moved_section" in structural_changes:
                return TableDiffType.MOVED
            if "caption_changed" in structural_changes:
                return TableDiffType.RENAMED
            if "split" in " ".join(structural_changes):
                return TableDiffType.STRUCTURAL_CHANGED

        # Column changes
        if column_additions or column_removals:
            return TableDiffType.COLUMN_CHANGED

        # Row changes (use provided counts, fall back to row_changes if needed)
        if rows_added > 0 or rows_removed > 0:
            return TableDiffType.ROW_CHANGED
        if row_changes.get("rows_added", 0) > 0 or row_changes.get("rows_removed", 0) > 0:
            return TableDiffType.ROW_CHANGED

        # Cell changes
        if rows_modified > 0:
            return TableDiffType.CELL_CHANGED
        if row_changes.get("rows_modified", 0) > 0:
            return TableDiffType.CELL_CHANGED

        return TableDiffType.IDENTICAL

    def _classify_diff_impact(self, diff: TableDiff) -> TableDiffImpact:
        """Classify impact severity of table diff.

        Returns:
            TableDiffImpact: IDENTICAL, LOW, MEDIUM, or HIGH
        """
        if diff.diff_type == TableDiffType.IDENTICAL:
            return TableDiffImpact.IDENTICAL

        # Check if content is identical (no cell modifications)
        content_identical = diff.cells_modified == 0 and diff.rows_modified == 0

        # Check if only caption/section changed
        structural_only = (
            diff.rows_added == 0
            and diff.rows_removed == 0
            and len(diff.column_additions) == 0
            and len(diff.column_removals) == 0
        )

        # LOW impact: Content identical but numbering/caption/section changed
        if content_identical and structural_only:
            structural_changes_set = set(diff.structural_changes)
            # Only caption and/or section changes, no other structural changes
            if structural_changes_set <= {"caption_changed", "moved_section"}:
                return TableDiffImpact.LOW

        # MEDIUM impact: Structure changed but content mostly preserved
        if (diff.rows_added > 0 or diff.rows_removed > 0) and diff.cells_modified < 5:
            return TableDiffImpact.MEDIUM

        # HIGH impact: Content modified or major restructuring
        if diff.cells_modified > 0 or diff.rows_modified > 0:
            return TableDiffImpact.HIGH

        # Fallback based on diff type
        if diff.diff_type in (TableDiffType.COLUMN_CHANGED, TableDiffType.ROW_CHANGED):
            return TableDiffImpact.MEDIUM

        return TableDiffImpact.HIGH

    @staticmethod
    def _cells_match(old_text: str, new_text: str) -> bool:
        """Check if two cell texts are equivalent."""
        # Normalize whitespace
        old_norm = " ".join(str(old_text).split())
        new_norm = " ".join(str(new_text).split())
        return old_norm.lower() == new_norm.lower()

    def compare_table_json(
        self,
        old_json: dict[str, Any],
        new_json: dict[str, Any],
    ) -> TableDiff:
        """Compare tables using their JSON representations.

        This method provides JSON-based comparison that preserves full structural
        information (rowspan/colspan, nested tables, cell types, formatting).

        Args:
            old_json: Canonical table JSON from table.to_dict()
            new_json: Canonical table JSON from table.to_dict()

        Returns:
            TableDiff with granular change information
        """
        # Extract table UIDs
        old_uid = old_json.get("table_uid", "unknown")
        new_uid = new_json.get("table_uid", "unknown")

        # Extract dimensions
        old_dims = old_json.get("dimensions", {})
        new_dims = new_json.get("dimensions", {})
        old_rows = old_json.get("rows", [])
        new_rows = new_json.get("rows", [])

        # Extract structural info
        old_section = old_json.get("section_path", [])
        new_section = new_json.get("section_path", [])
        old_caption = old_json.get("caption_normalized", "")
        new_caption = new_json.get("caption_normalized", "")

        # Compute similarity using JSON structure
        old_json_str = str(sorted(old_json.items()))
        new_json_str = str(sorted(new_json.items()))
        similarity = self._compute_json_similarity(old_json_str, new_json_str)

        # Detect structural changes
        structural_changes = []
        if old_section != new_section:
            structural_changes.append("moved_section")
        if old_caption != new_caption:
            structural_changes.append("caption_changed")
        if old_dims.get("num_cols") != new_dims.get("num_cols"):
            structural_changes.append("columns_changed")
        if old_json.get("split_info", {}).get("is_split") != new_json.get("split_info", {}).get("is_split"):
            structural_changes.append("split_status_changed")

        # Compare rows
        rows_added = max(0, len(new_rows) - len(old_rows))
        rows_removed = max(0, len(old_rows) - len(new_rows))

        # Count modified rows by comparing row JSONs
        rows_modified = 0
        for i, (old_row_json, new_row_json) in enumerate(
            zip(old_rows, new_rows)
        ):
            if old_row_json != new_row_json:
                rows_modified += 1

        # Count modified cells by comparing row-by-row
        cells_modified = 0
        for old_row_json, new_row_json in zip(old_rows, new_rows):
            old_cells = old_row_json.get("cells", [])
            new_cells = new_row_json.get("cells", [])
            for old_cell, new_cell in zip(old_cells, new_cells):
                if old_cell != new_cell:
                    cells_modified += 1

        # Determine diff type
        diff_type = self._determine_diff_type(
            similarity=similarity,
            column_additions=[],
            column_removals=[],
            structural_changes=structural_changes,
            row_changes={"rows_added": rows_added, "rows_removed": rows_removed},
            rows_added=rows_added,
            rows_removed=rows_removed,
            rows_modified=rows_modified,
        )

        # Create table diff
        diff = TableDiff(
            table_uid=old_uid if old_uid != "unknown" else new_uid,
            diff_type=diff_type,
            old_table=None,  # JSON-based, no table objects
            new_table=None,
            row_diffs=[],  # Would need to reconstruct from JSON
            similarity_score=similarity,
            rows_added=rows_added,
            rows_removed=rows_removed,
            rows_modified=rows_modified,
            cells_modified=cells_modified,
            structural_changes=structural_changes,
            metadata={
                "comparison_method": "json_based",
                "old_rows": len(old_rows),
                "new_rows": len(new_rows),
            },
        )

        # Classify impact
        diff.diff_impact = self._classify_json_diff_impact(diff)
        return diff

    @staticmethod
    def _compute_json_similarity(old_str: str, new_str: str) -> float:
        """Compute similarity between two JSON string representations.

        Uses simple character-level comparison as a heuristic.

        Args:
            old_str: String representation of old JSON
            new_str: String representation of new JSON

        Returns:
            Similarity score between 0.0 and 1.0
        """
        # Simple approach: count matching characters
        if not old_str or not new_str:
            return 0.0

        matches = sum(
            1 for o, n in zip(old_str, new_str) if o == n
        )
        max_len = max(len(old_str), len(new_str))
        return matches / max_len if max_len > 0 else 0.0

    @staticmethod
    def _classify_json_diff_impact(diff: TableDiff) -> TableDiffImpact:
        """Classify impact severity using JSON diff information.

        Args:
            diff: TableDiff computed from JSON comparison

        Returns:
            TableDiffImpact severity level
        """
        if diff.diff_type == TableDiffType.IDENTICAL:
            return TableDiffImpact.IDENTICAL

        # Check if only structural metadata changed
        content_identical = diff.cells_modified == 0 and diff.rows_modified == 0
        structural_only = (
            diff.rows_added == 0
            and diff.rows_removed == 0
        )

        # LOW impact: content identical, only metadata/numbering changed
        if content_identical and structural_only:
            changes_set = set(diff.structural_changes)
            if changes_set <= {"caption_changed", "moved_section"}:
                return TableDiffImpact.LOW

        # MEDIUM impact: structure changed but <5 cell modifications
        if diff.cells_modified < 5:
            return TableDiffImpact.MEDIUM

        # HIGH impact: significant content changes
        return TableDiffImpact.HIGH


class TableMatchingEngine:
    """Match old and new tables for comparison."""

    def __init__(self):
        """Initialize matcher."""
        self.diff_engine = TableDiffEngine()

    def match_tables(
        self,
        old_tables: dict[str, CanonicalTable],
        new_tables: dict[str, CanonicalTable],
    ) -> dict[str, TableDiff]:
        """Match old and new tables and compute diffs.

        Uses table_uid as primary key, falls back to semantic matching.

        Args:
            old_tables: Mapping of table_uid to old CanonicalTable
            new_tables: Mapping of table_uid to new CanonicalTable

        Returns:
            Mapping of table_uid to TableDiff
        """
        diffs: dict[str, TableDiff] = {}

        # Direct UUID matches
        matched_new_uids = set()

        for uid, old_table in old_tables.items():
            if uid in new_tables:
                # Direct match
                new_table = new_tables[uid]
                diff = self.diff_engine.diff_tables(old_table, new_table)
                diffs[uid] = diff
                matched_new_uids.add(uid)
            else:
                # Try semantic matching
                best_match = self._find_semantic_match(old_table, new_tables)
                if best_match:
                    new_table, match_score = best_match
                    diff = self.diff_engine.diff_tables(old_table, new_table)
                    diff.metadata["semantic_match_score"] = match_score
                    diffs[uid] = diff
                    matched_new_uids.add(new_table.table_uid)
                else:
                    # Table was removed
                    diff = TableDiff(
                        table_uid=old_table.table_uid,
                        diff_type=TableDiffType.IDENTICAL,
                        old_table=old_table,
                        new_table=None,
                    )
                    diffs[uid] = diff

        # Add new tables that weren't matched
        for uid, new_table in new_tables.items():
            if uid not in matched_new_uids:
                diff = TableDiff(
                    table_uid=new_table.table_uid,
                    diff_type=TableDiffType.IDENTICAL,
                    old_table=None,
                    new_table=new_table,
                )
                diffs[uid] = diff

        return diffs

    def _find_semantic_match(
        self,
        old_table: CanonicalTable,
        new_tables: dict[str, CanonicalTable],
    ) -> tuple[CanonicalTable, float] | None:
        """Find semantically similar table for unmatched old table."""
        if not new_tables:
            return None

        best_match = None
        best_score = 0.7  # Minimum similarity threshold

        for new_table in new_tables.values():
            score = self.diff_engine._compute_table_similarity(old_table, new_table)

            if score > best_score:
                best_score = score
                best_match = new_table

        return (best_match, best_score) if best_match else None
