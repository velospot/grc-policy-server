"""img2table extractor for lightweight OpenCV-based table extraction."""

from __future__ import annotations

import logging

from grc_policy_server.services.ingestion.backends.base_extractor import TableExtractor
from grc_policy_server.services.ingestion.table_extraction_ensemble import TableCandidate

logger = logging.getLogger(__name__)


class Img2tableTableExtractor(TableExtractor):
    """Extract tables using img2table (lightweight OpenCV-based extraction).

    Useful as a fallback on CPU-heavy or image-based table cases.
    """

    async def extract(
        self,
        pdf_path: str,
        page_numbers: list[int] | None = None,
    ) -> list[TableCandidate]:
        """Extract tables using img2table.

        Args:
            pdf_path: Path to PDF file
            page_numbers: Optional list of specific pages to process

        Returns:
            List of TableCandidate objects
        """
        try:
            # img2table integration will be implemented in next phase
            logger.info("img2table extractor not yet implemented")
            return []

        except Exception as e:
            logger.warning(f"img2table extraction failed: {e}")
            return []
