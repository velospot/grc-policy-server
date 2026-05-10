"""Base class for table extractors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from grc_policy_server.services.ingestion.table_extraction_ensemble import TableCandidate


class TableExtractor(ABC):
    """Abstract base class for all table extraction backends."""

    @abstractmethod
    async def extract(
        self,
        pdf_path: str,
        page_numbers: list[int] | None = None,
    ) -> list[TableCandidate]:
        """Extract tables from a PDF.

        Args:
            pdf_path: Path to PDF file
            page_numbers: Optional list of specific pages to extract (None = all)

        Returns:
            List of TableCandidate objects
        """
        pass

    @staticmethod
    def cells_from_rows(rows: list[list[str | None]]) -> list[dict[str, Any]]:
        """Convert pdfplumber-style row list to canonical cell dicts.

        Args:
            rows: List of rows, where each row is a list of cell texts

        Returns:
            List of cell dicts with row, col, text, rowspan, colspan, is_header
        """
        if not rows:
            return []

        cells = []
        for row_idx, row in enumerate(rows):
            for col_idx, text in enumerate(row or []):
                cells.append({
                    "row": row_idx,
                    "col": col_idx,
                    "text": (text or "").strip(),
                    "rowspan": 1,
                    "colspan": 1,
                    "is_header": row_idx == 0,
                })

        return cells

    @staticmethod
    def extract_headers(cells: list[dict[str, Any]]) -> list[str]:
        """Extract header texts from cells (first row)."""
        header_cells = sorted(
            [c for c in cells if c.get("is_header")],
            key=lambda c: c.get("col", 0),
        )
        return [str(c.get("text", "")) for c in header_cells]
