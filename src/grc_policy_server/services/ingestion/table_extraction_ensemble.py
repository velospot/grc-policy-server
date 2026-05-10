"""Multi-backend table extraction ensemble for compliance PDFs.

Combines GMFT, Surya OCR, Camelot, pdfplumber, and img2table to produce
high-quality table candidates with confidence scoring and reconciliation.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TableCell:
    """Canonical representation of a table cell."""

    row: int
    col: int
    text: str
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False
    bbox: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TableCandidate:
    """A table extraction candidate from one backend."""

    backend_name: str
    page_number: int
    bbox: dict[str, float]  # {x0, y0, x1, y1}
    cells: list[dict[str, Any]]  # List of cell dicts with row, col, text, rowspan, colspan
    headers: list[str]
    num_rows: int
    num_cols: int
    confidence: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)

    def cell_grid(self) -> dict[tuple[int, int], str]:
        """Return mapping of (row, col) to cell text for overlap detection."""
        grid = {}
        for cell in self.cells:
            r = cell.get("row", 0)
            c = cell.get("col", 0)
            grid[(r, c)] = str(cell.get("text", "")).strip()
        return grid

    def column_signature(self) -> str:
        """Return normalized column header signature for matching."""
        if not self.headers:
            return ""
        normalized = [str(h).lower().strip() for h in self.headers]
        return "|".join(normalized)


@dataclass
class TableExtractorEnsemble:
    """Orchestrates parallel extraction from multiple backends and reconciles results."""

    use_gmft: bool = True
    use_surya: bool = True
    use_camelot: bool = True
    use_pdfplumber: bool = True
    use_img2table: bool = False
    min_confidence_threshold: float = 0.3
    reconciliation_overlap_threshold: float = 0.7

    async def extract_tables(
        self,
        pdf_path: str,
        page_numbers: list[int] | None = None,
    ) -> list[TableCandidate]:
        """Extract tables from PDF using available backends in parallel.

        Args:
            pdf_path: Path to PDF file
            page_numbers: List of specific pages to extract from (None = all)

        Returns:
            List of reconciled table candidates, sorted by confidence
        """
        # Gather all backend tasks
        tasks = []

        if self.use_gmft:
            tasks.append(self._extract_with_gmft(pdf_path, page_numbers))
        if self.use_surya:
            tasks.append(self._extract_with_surya(pdf_path, page_numbers))
        if self.use_camelot:
            tasks.append(self._extract_with_camelot(pdf_path, page_numbers))
        if self.use_pdfplumber:
            tasks.append(self._extract_with_pdfplumber(pdf_path, page_numbers))
        if self.use_img2table:
            tasks.append(self._extract_with_img2table(pdf_path, page_numbers))

        if not tasks:
            logger.warning("No extraction backends enabled")
            return []

        # Run all extractors in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect all candidates
        all_candidates: list[TableCandidate] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Extraction error: {result}")
                continue
            if isinstance(result, list):
                all_candidates.extend(result)

        logger.info(
            f"Extracted {len(all_candidates)} table candidates from {len(tasks)} backends"
        )

        # Reconcile duplicate detections
        reconciled = self._reconcile_candidates(all_candidates)

        # Filter by confidence
        filtered = [c for c in reconciled if c.confidence >= self.min_confidence_threshold]

        # Sort by page, then by x-position
        filtered.sort(key=lambda c: (c.page_number, c.bbox.get("x0", 0)))

        logger.info(f"Reconciled to {len(filtered)} unique tables after deduplication")
        return filtered

    async def _extract_with_gmft(
        self,
        pdf_path: str,
        page_numbers: list[int] | None,
    ) -> list[TableCandidate]:
        """Extract using GMFT (Microsoft Table Transformer)."""
        try:
            from grc_policy_server.services.ingestion.backends.gmft_extractor import (
                GmftTableExtractor,
            )

            extractor = GmftTableExtractor()
            candidates = await extractor.extract(pdf_path, page_numbers)
            logger.debug(f"GMFT extracted {len(candidates)} tables")
            return candidates
        except ImportError:
            logger.debug("GMFT not available (optional backend)")
            return []
        except Exception as e:
            logger.warning(f"GMFT extraction failed: {e}")
            return []

    async def _extract_with_surya(
        self,
        pdf_path: str,
        page_numbers: list[int] | None,
    ) -> list[TableCandidate]:
        """Extract using Surya OCR + layout analysis."""
        try:
            from grc_policy_server.services.ingestion.backends.surya_extractor import (
                SuryaTableExtractor,
            )

            extractor = SuryaTableExtractor()
            candidates = await extractor.extract(pdf_path, page_numbers)
            logger.debug(f"Surya extracted {len(candidates)} tables")
            return candidates
        except ImportError:
            logger.debug("Surya not available (optional backend)")
            return []
        except Exception as e:
            logger.warning(f"Surya extraction failed: {e}")
            return []

    async def _extract_with_camelot(
        self,
        pdf_path: str,
        page_numbers: list[int] | None,
    ) -> list[TableCandidate]:
        """Extract using Camelot (text-based ruled/stream tables)."""
        try:
            from grc_policy_server.services.ingestion.backends.camelot_extractor import (
                CamelotTableExtractor,
            )

            extractor = CamelotTableExtractor()
            candidates = await extractor.extract(pdf_path, page_numbers)
            logger.debug(f"Camelot extracted {len(candidates)} tables")
            return candidates
        except ImportError:
            logger.debug("Camelot not available (optional backend)")
            return []
        except Exception as e:
            logger.warning(f"Camelot extraction failed: {e}")
            return []

    async def _extract_with_pdfplumber(
        self,
        pdf_path: str,
        page_numbers: list[int] | None,
    ) -> list[TableCandidate]:
        """Extract using pdfplumber (coordinate-based extraction)."""
        try:
            from grc_policy_server.services.ingestion.backends.pdfplumber_extractor import (
                PdfplumberTableExtractor,
            )

            extractor = PdfplumberTableExtractor()
            candidates = await extractor.extract(pdf_path, page_numbers)
            logger.debug(f"pdfplumber extracted {len(candidates)} tables")
            return candidates
        except ImportError:
            logger.debug("pdfplumber not available (should be installed)")
            return []
        except Exception as e:
            logger.warning(f"pdfplumber extraction failed: {e}")
            return []

    async def _extract_with_img2table(
        self,
        pdf_path: str,
        page_numbers: list[int] | None,
    ) -> list[TableCandidate]:
        """Extract using img2table (lightweight OpenCV-based)."""
        try:
            from grc_policy_server.services.ingestion.backends.img2table_extractor import (
                Img2tableTableExtractor,
            )

            extractor = Img2tableTableExtractor()
            candidates = await extractor.extract(pdf_path, page_numbers)
            logger.debug(f"img2table extracted {len(candidates)} tables")
            return candidates
        except ImportError:
            logger.debug("img2table not available (optional backend)")
            return []
        except Exception as e:
            logger.warning(f"img2table extraction failed: {e}")
            return []

    def _reconcile_candidates(self, candidates: list[TableCandidate]) -> list[TableCandidate]:
        """Merge candidates with high structural similarity.

        Groups candidates by page and bbox proximity, then selects best from each group
        based on confidence and header quality.
        """
        if not candidates:
            return []

        # Group by page
        by_page: dict[int, list[TableCandidate]] = {}
        for cand in candidates:
            page = cand.page_number
            if page not in by_page:
                by_page[page] = []
            by_page[page].append(cand)

        reconciled: list[TableCandidate] = []

        for page, page_candidates in by_page.items():
            # Sort by confidence (descending)
            page_candidates = sorted(page_candidates, key=lambda c: -c.confidence)

            # Group by spatial proximity
            used = set()
            for i, cand in enumerate(page_candidates):
                if i in used:
                    continue

                # Find all duplicates of this table (by bbox overlap)
                duplicates = [cand]
                for j, other in enumerate(page_candidates[i + 1 :], start=i + 1):
                    if j in used:
                        continue
                    if self._bbox_overlap(cand.bbox, other.bbox) >= 0.8:
                        duplicates.append(other)
                        used.add(j)

                # Select best candidate from duplicates
                best = self._select_best_candidate(duplicates)
                reconciled.append(best)
                used.add(i)

        return reconciled

    @staticmethod
    def _bbox_overlap(bbox1: dict[str, float], bbox2: dict[str, float]) -> float:
        """Calculate intersection-over-union (IoU) of two bboxes."""
        x0_1, y0_1, x1_1, y1_1 = (
            bbox1.get("x0", 0),
            bbox1.get("y0", 0),
            bbox1.get("x1", 0),
            bbox1.get("y1", 0),
        )
        x0_2, y0_2, x1_2, y1_2 = (
            bbox2.get("x0", 0),
            bbox2.get("y0", 0),
            bbox2.get("x1", 0),
            bbox2.get("y1", 0),
        )

        # Intersection
        xi0 = max(x0_1, x0_2)
        yi0 = max(y0_1, y0_2)
        xi1 = min(x1_1, x1_2)
        yi1 = min(y1_1, y1_2)

        if xi0 >= xi1 or yi0 >= yi1:
            return 0.0

        intersection = (xi1 - xi0) * (yi1 - yi0)
        union = (x1_1 - x0_1) * (y1_1 - y0_1) + (x1_2 - x0_2) * (y1_2 - y0_2) - intersection

        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _select_best_candidate(candidates: list[TableCandidate]) -> TableCandidate:
        """Select best candidate from a group of duplicates.

        Prioritizes: confidence, column quality, header count.
        """
        if not candidates:
            return candidates[0]

        def score(c: TableCandidate) -> tuple[float, int, float]:
            # (confidence, header_count, column_quality_metric)
            header_quality = len([h for h in c.headers if h and not str(h).startswith("column_")])
            col_quality = header_quality / max(c.num_cols, 1)
            return (c.confidence, header_quality, col_quality)

        return max(candidates, key=score)
