"""Canonical table model with cell graph, rowspan/colspan, and nested table support.

Replaces Markdown representation with semantic cell graph that preserves:
- Merged cells (rowspan, colspan)
- Nested tables in cells
- Bounding box and page/section provenance
- HTML + JSON dual representation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from enum import Enum


class CellType(str, Enum):
    """Type of cell content."""

    TEXT = "text"
    NESTED_TABLE = "nested_table"
    IMAGE = "image"
    FORMULA = "formula"
    MIXED = "mixed"


@dataclass(frozen=True)
class BBox:
    """Bounding box coordinates in PDF page space."""

    x0: float
    y0: float
    x1: float
    y1: float
    page: int

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    def to_dict(self) -> dict[str, Any]:
        return {
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
            "page": self.page,
        }


@dataclass(frozen=True)
class TableColumn:
    """Table column metadata."""

    index: int
    name: str  # Original header text
    normalized: str  # Normalized for comparison
    width: float | None = None
    data_type: str = "text"  # text, number, date, etc.

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "normalized": self.normalized,
            "width": self.width,
            "data_type": self.data_type,
        }


@dataclass
class TableCell:
    """Cell in a canonical table with rich metadata."""

    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    text: str = ""
    cell_type: CellType = CellType.TEXT
    is_header: bool = False
    bbox: BBox | None = None
    # Nested table or other structured content
    children: list[CanonicalTable] = field(default_factory=list)
    # Formatting metadata
    bold: bool = False
    italic: bool = False
    underline: bool = False
    background_color: str | None = None
    # Original source references
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "col": self.col,
            "rowspan": self.rowspan,
            "colspan": self.colspan,
            "text": self.text,
            "cell_type": self.cell_type.value,
            "is_header": self.is_header,
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "children": [child.to_dict() for child in self.children],
            "formatting": {
                "bold": self.bold,
                "italic": self.italic,
                "underline": self.underline,
                "background_color": self.background_color,
            },
            "metadata": self.metadata,
        }


@dataclass
class TableRow:
    """Row in a canonical table."""

    row_number: int
    cells: list[TableCell] = field(default_factory=list)
    row_uid: str | None = None  # Row key for compliance docs (e.g., REQ-001:TC-02)
    row_fingerprint: str = ""  # Hash of row content for matching
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "cells": [cell.to_dict() for cell in self.cells],
            "row_uid": self.row_uid,
            "row_fingerprint": self.row_fingerprint,
            "metadata": self.metadata,
        }


@dataclass
class CanonicalTable:
    """Canonical representation of a table with full semantic information."""

    table_uid: str
    caption_original: str
    caption_normalized: str
    section_path: list[str]  # ["Security", "Access Control"]
    pages: list[int]
    columns: list[TableColumn]
    rows: list[TableRow]
    # Table-level metadata
    num_rows: int = 0
    num_cols: int = 0
    bbox: BBox | None = None
    is_split: bool = False
    split_across_pages: list[int] = field(default_factory=list)
    # Representations
    html_repr: str = ""
    json_repr: dict[str, Any] = field(default_factory=dict)
    markdown_repr: str = ""
    # Source information
    extraction_backend: str = "unknown"
    confidence: float = 0.5
    # Fingerprints
    structure_hash: str = ""
    content_hash: str = ""
    # Rich metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate and initialize table."""
        if not self.table_uid:
            raise ValueError("table_uid cannot be empty")
        if not self.pages:
            raise ValueError("pages must not be empty")

        # Auto-compute dimensions if not set
        if self.num_rows == 0:
            self.num_rows = len(self.rows)
        if self.num_cols == 0 and self.columns:
            self.num_cols = len(self.columns)

    def get_cell(self, row: int, col: int) -> TableCell | None:
        """Get cell at specific row/column position."""
        if row < 0 or row >= len(self.rows):
            return None
        row_obj = self.rows[row]
        for cell in row_obj.cells:
            if cell.col == col:
                return cell
        return None

    def cell_grid(self) -> dict[tuple[int, int], str]:
        """Return mapping of (row, col) to cell text for comparison."""
        grid = {}
        for row in self.rows:
            for cell in row.cells:
                # Account for merged cells
                for r in range(cell.row, cell.row + cell.rowspan):
                    for c in range(cell.col, cell.col + cell.colspan):
                        grid[(r, c)] = cell.text.strip()
        return grid

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "table_uid": self.table_uid,
            "caption_original": self.caption_original,
            "caption_normalized": self.caption_normalized,
            "section_path": self.section_path,
            "pages": self.pages,
            "columns": [col.to_dict() for col in self.columns],
            "rows": [row.to_dict() for row in self.rows],
            "dimensions": {
                "num_rows": self.num_rows,
                "num_cols": self.num_cols,
            },
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "split_info": {
                "is_split": self.is_split,
                "split_across_pages": self.split_across_pages,
            },
            "source": {
                "extraction_backend": self.extraction_backend,
                "confidence": self.confidence,
            },
            "hashes": {
                "structure_hash": self.structure_hash,
                "content_hash": self.content_hash,
            },
            "representations": {
                "html": self.html_repr,
                "markdown": self.markdown_repr,
            },
            "metadata": self.metadata,
        }

    def to_html(self) -> str:
        """Generate HTML representation of table."""
        if self.html_repr:
            return self.html_repr

        html_parts = ['<table class="canonical-table">']

        # Header row
        if self.columns:
            html_parts.append("<thead><tr>")
            for col in self.columns:
                html_parts.append(f'<th>{self._escape_html(col.name)}</th>')
            html_parts.append("</tr></thead>")

        # Data rows
        html_parts.append("<tbody>")
        for row in self.rows:
            html_parts.append("<tr>")
            for cell in row.cells:
                attrs = []
                if cell.rowspan > 1:
                    attrs.append(f'rowspan="{cell.rowspan}"')
                if cell.colspan > 1:
                    attrs.append(f'colspan="{cell.colspan}"')

                style_parts = []
                if cell.bold:
                    style_parts.append("font-weight: bold")
                if cell.italic:
                    style_parts.append("font-style: italic")
                if cell.underline:
                    style_parts.append("text-decoration: underline")
                if cell.background_color:
                    style_parts.append(f"background-color: {cell.background_color}")

                style = f' style="{"; ".join(style_parts)}"' if style_parts else ""
                attrs_str = " ".join(attrs)
                tag = "th" if cell.is_header else "td"
                html_parts.append(
                    f'<{tag} {attrs_str}{style}>{self._escape_html(cell.text)}</{tag}>'
                )

            html_parts.append("</tr>")
        html_parts.append("</tbody>")

        html_parts.append("</table>")
        return "\n".join(html_parts)

    def to_markdown(self) -> str:
        """Generate Markdown representation of table."""
        if self.markdown_repr:
            return self.markdown_repr

        lines = []

        # Header row
        if self.columns:
            header = "| " + " | ".join(col.name for col in self.columns) + " |"
            separator = "| " + " | ".join("---" for _ in self.columns) + " |"
            lines.append(header)
            lines.append(separator)

        # Data rows (simplified - doesn't handle rowspan/colspan)
        for row in self.rows:
            cells = [""] * len(self.columns)
            for cell in row.cells:
                if cell.col < len(cells):
                    cells[cell.col] = cell.text.replace("\n", " ")
            lines.append("| " + " | ".join(cells) + " |")

        return "\n".join(lines)

    def to_json(self) -> str:
        """Export table to JSON string with full structure information.

        Includes cell structure (rowspan/colspan), nested tables, cell types,
        formatting metadata, and section path + table_uid for identity.

        Returns:
            JSON string representation of canonical table
        """
        import json

        return json.dumps(self.to_dict(), indent=2, default=str)

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )


def canonical_tables_from_ensemble_candidates(
    candidates: list[Any],  # TableCandidate objects
    identities: dict[str, Any],  # TableIdentity objects
) -> dict[str, CanonicalTable]:
    """Convert ensemble candidates and identities to canonical tables.

    Args:
        candidates: List of TableCandidate from ensemble
        identities: Dict of TableIdentity from resolver

    Returns:
        Dict mapping table_uid to CanonicalTable
    """
    canonical_tables: dict[str, CanonicalTable] = {}

    for table_uid, identity in identities.items():
        # Find candidates for this table
        related_candidates = [
            c for c in candidates
            if c.page_number in identity.pages
            and c.num_cols == identity.column_signature.count("|") + 1
        ]

        if not related_candidates:
            continue

        # Select best candidate as canonical source
        canonical_cand = max(related_candidates, key=lambda c: c.confidence)

        # Build canonical table structure
        columns = [
            TableColumn(i, header, header.lower())
            for i, header in enumerate(canonical_cand.headers)
        ]

        rows = []
        for cell_dict in canonical_cand.cells:
            row_num = cell_dict.get("row", 0)

            # Ensure row exists
            while len(rows) <= row_num:
                rows.append(TableRow(len(rows)))

            # Create cell
            cell = TableCell(
                row=cell_dict.get("row", 0),
                col=cell_dict.get("col", 0),
                rowspan=cell_dict.get("rowspan", 1),
                colspan=cell_dict.get("colspan", 1),
                text=cell_dict.get("text", ""),
                is_header=cell_dict.get("is_header", False),
            )

            rows[row_num].cells.append(cell)

        # Create canonical table
        table = CanonicalTable(
            table_uid=table_uid,
            caption_original=identity.caption_original,
            caption_normalized=identity.caption_normalized,
            section_path=identity.section_path,
            pages=identity.pages,
            columns=columns,
            rows=rows,
            num_rows=len(rows),
            num_cols=len(columns),
            is_split=identity.is_split,
            extraction_backend=canonical_cand.backend_name,
            confidence=canonical_cand.confidence,
            structure_hash=identity.structure_hash,
            content_hash=identity.content_hash,
            metadata={
                "continuation_signals": identity.continuation_signals,
                "all_backends": [c.backend_name for c in related_candidates],
            },
        )

        # Generate representations
        table.html_repr = table.to_html()
        table.markdown_repr = table.to_markdown()

        canonical_tables[table_uid] = table

    return canonical_tables
