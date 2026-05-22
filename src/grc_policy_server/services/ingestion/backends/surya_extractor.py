"""Surya OCR extractor for table detection and layout analysis.

Surya provides OCR, layout detection, and reading order analysis.
Useful for scanned PDFs and visually complex documents.
"""

from __future__ import annotations

import asyncio
import logging

from grc_policy_server.services.ingestion.backends.base_extractor import TableExtractor
from grc_policy_server.services.ingestion.table_extraction_ensemble import TableCandidate

logger = logging.getLogger(__name__)


class SuryaTableExtractor(TableExtractor):
    """Extract tables using Surya OCR and layout analysis."""

    async def extract(
        self,
        pdf_path: str,
        page_numbers: list[int] | None = None,
    ) -> list[TableCandidate]:
        """Extract tables using Surya.

        Args:
            pdf_path: Path to PDF file
            page_numbers: Optional list of specific pages to process

        Returns:
            List of TableCandidate objects
        """
        try:
            # Surya integration will be implemented in next phase
            logger.info("Surya extractor not yet implemented")
            return []

        except Exception as e:
            logger.warning(f"Surya extraction failed: {e}")
            return []
