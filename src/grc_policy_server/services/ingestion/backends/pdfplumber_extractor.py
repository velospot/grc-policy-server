"""pdfplumber extractor for coordinate-based table extraction."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from grc_policy_server.services.ingestion.backends.base_extractor import TableExtractor
from grc_policy_server.services.ingestion.table_extraction_ensemble import TableCandidate

logger = logging.getLogger(__name__)


class PdfplumberTableExtractor(TableExtractor):
    """Extract tables using pdfplumber (coordinate-based extraction).

    pdfplumber provides low-level access to PDF content, coordinates,
    and line geometry. Useful for verification and reconciliation.
    """

    async def extract(
        self,
        pdf_path: str,
        page_numbers: list[int] | None = None,
    ) -> list[TableCandidate]:
        """Extract tables using pdfplumber.

        Args:
            pdf_path: Path to PDF file
            page_numbers: Optional list of specific pages to process

        Returns:
            List of TableCandidate objects
        """
        try:
            import pdfplumber

            candidates: list[TableCandidate] = []

            with pdfplumber.open(pdf_path) as pdf:
                pages_to_process = page_numbers if page_numbers else range(len(pdf.pages))

                for page_num in pages_to_process:
                    if page_num < 0 or page_num >= len(pdf.pages):
                        continue

                    page = pdf.pages[page_num]
                    tables = page.extract_tables()

                    if not tables:
                        continue

                    for table_idx, rows in enumerate(tables):
                        # Convert rows to cells
                        cells = self.cells_from_rows(rows)
                        if not cells:
                            continue

                        # Calculate num_rows and num_cols
                        num_rows = len(rows)
                        num_cols = max(len(row) for row in rows) if rows else 0

                        # Extract headers (first row)
                        headers = self.extract_headers(cells)

                        # Calculate bbox from table position
                        table_bbox = page.extract_table_settings(explicit_vertical_lines=True)[
                            table_idx
                        ]
                        if table_bbox:
                            bbox = {
                                "x0": table_bbox[0][0],
                                "y0": table_bbox[0][1],
                                "x1": table_bbox[1][0],
                                "y1": table_bbox[1][1],
                            }
                        else:
                            # Estimate from cells
                            bbox = {
                                "x0": 0,
                                "y0": 0,
                                "x1": page.width,
                                "y1": page.height,
                            }

                        candidate = TableCandidate(
                            backend_name="pdfplumber",
                            page_number=page_num + 1,
                            bbox=bbox,
                            cells=cells,
                            headers=headers,
                            num_rows=num_rows,
                            num_cols=num_cols,
                            confidence=0.7,  # pdfplumber is reliable for ruled tables
                            metadata={
                                "table_index": table_idx,
                                "extraction_method": "pdfplumber.extract_tables",
                            },
                        )
                        candidates.append(candidate)

            logger.info(
                f"pdfplumber extracted {len(candidates)} tables from {len(pages_to_process)} pages"
            )
            return candidates

        except ImportError:
            logger.error("pdfplumber not installed")
            return []
        except Exception as e:
            logger.warning(f"pdfplumber extraction failed: {e}")
            return []

    async def _get_table_bbox_from_page(self, page: Any, table_idx: int) -> dict[str, float]:
        """Estimate table bounding box from PDF page structure."""
        try:
            # Try to get bbox from page tables metadata
            if hasattr(page, "tables") and table_idx < len(page.tables):
                bbox = page.tables[table_idx].bbox
                return {
                    "x0": bbox[0],
                    "y0": bbox[1],
                    "x1": bbox[2],
                    "y1": bbox[3],
                }
        except Exception:
            pass

        # Fallback: use page dimensions
        return {
            "x0": 0,
            "y0": 0,
            "x1": page.width if hasattr(page, "width") else 612,
            "y1": page.height if hasattr(page, "height") else 792,
        }
