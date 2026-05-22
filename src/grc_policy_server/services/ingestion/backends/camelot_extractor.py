"""Camelot extractor for text-based ruled and stream tables."""

from __future__ import annotations

import logging
from typing import Any

from grc_policy_server.services.ingestion.backends.base_extractor import TableExtractor
from grc_policy_server.services.ingestion.table_extraction_ensemble import TableCandidate

logger = logging.getLogger(__name__)


class CamelotTableExtractor(TableExtractor):
    """Extract tables using Camelot (fast text-based extraction)."""

    async def extract(
        self,
        pdf_path: str,
        page_numbers: list[int] | None = None,
    ) -> list[TableCandidate]:
        """Extract tables using Camelot.

        Attempts both 'stream' (heuristic, handles borderless) and 'lattice' (grid-based)
        modes, returning candidates from the mode with better detection.

        Args:
            pdf_path: Path to PDF file
            page_numbers: Optional list of specific pages to process

        Returns:
            List of TableCandidate objects
        """
        try:
            import camelot

            candidates: list[TableCandidate] = []

            # Determine pages to process
            if page_numbers:
                # Camelot expects page strings like "1,2,3" or "1-3"
                pages_str = ",".join(str(p) for p in sorted(page_numbers))
            else:
                pages_str = "all"

            # Try stream mode first (better for borderless/informal tables)
            try:
                stream_tables = camelot.read_pdf(
                    pdf_path,
                    pages=pages_str,
                    flavor="stream",
                    suppress_stdout=True,
                )
                logger.debug(f"Camelot stream mode found {len(stream_tables)} tables")
            except Exception as e:
                logger.debug(f"Camelot stream mode failed: {e}")
                stream_tables = []

            # Try lattice mode (better for ruled/gridded tables)
            try:
                lattice_tables = camelot.read_pdf(
                    pdf_path,
                    pages=pages_str,
                    flavor="lattice",
                    suppress_stdout=True,
                )
                logger.debug(f"Camelot lattice mode found {len(lattice_tables)} tables")
            except Exception as e:
                logger.debug(f"Camelot lattice mode failed: {e}")
                lattice_tables = []

            # Combine results, preferring lattice for well-formed tables
            all_tables = list(stream_tables) + list(lattice_tables)
            if not all_tables:
                logger.debug("Camelot extracted no tables")
                return []

            # Convert Camelot tables to TableCandidate
            for camelot_table in all_tables:
                try:
                    candidate = self._camelot_to_candidate(camelot_table)
                    if candidate:
                        candidates.append(candidate)
                except Exception as e:
                    logger.warning(f"Failed to convert Camelot table: {e}")
                    continue

            logger.info(f"Camelot extracted {len(candidates)} valid candidates")
            return candidates

        except ImportError:
            logger.debug("Camelot not installed (optional backend)")
            return []
        except Exception as e:
            logger.warning(f"Camelot extraction failed: {e}")
            return []

    def _camelot_to_candidate(self, camelot_table: Any) -> TableCandidate | None:
        """Convert Camelot table object to TableCandidate.

        Args:
            camelot_table: A camelot.core.Table object

        Returns:
            TableCandidate or None if conversion fails
        """
        try:
            import camelot

            # Get table data
            df = camelot_table.df
            if df.empty or len(df) == 0:
                return None

            num_rows = len(df)
            num_cols = len(df.columns)

            if num_rows < 1 or num_cols < 1:
                return None

            # Extract headers (first row if reasonable, else column names)
            headers = [str(h).strip() for h in df.iloc[0].values]
            if not any(headers):
                headers = [str(i) for i in range(num_cols)]

            # Build cells from dataframe
            cells = []
            for row_idx in range(num_rows):
                for col_idx in range(num_cols):
                    text = str(df.iloc[row_idx, col_idx]).strip()
                    cells.append({
                        "row": row_idx,
                        "col": col_idx,
                        "text": text,
                        "rowspan": 1,
                        "colspan": 1,
                        "is_header": row_idx == 0,
                    })

            # Get bounding box (Camelot provides rect coordinates)
            bbox = self._get_bbox_from_camelot(camelot_table)

            # Camelot doesn't provide confidence, estimate based on consistency
            confidence = self._estimate_confidence(df, num_rows, num_cols)

            candidate = TableCandidate(
                backend_name="camelot",
                page_number=camelot_table.page_number,
                bbox=bbox,
                cells=cells,
                headers=headers,
                num_rows=num_rows,
                num_cols=num_cols,
                confidence=confidence,
                metadata={
                    "extraction_mode": getattr(camelot_table, "flavor", "unknown"),
                    "accuracy": getattr(camelot_table, "accuracy", 0),
                },
            )
            return candidate

        except Exception as e:
            logger.warning(f"Camelot table conversion failed: {e}")
            return None

    @staticmethod
    def _get_bbox_from_camelot(camelot_table: Any) -> dict[str, float]:
        """Extract bounding box from Camelot table.

        Args:
            camelot_table: Camelot table object

        Returns:
            Dictionary with x0, y0, x1, y1 coordinates
        """
        try:
            # Camelot provides bbox as (x0, top, x1, bottom)
            # where top/bottom are from page top in points
            if hasattr(camelot_table, "bbox"):
                x0, top, x1, bottom = camelot_table.bbox
                return {
                    "x0": float(x0),
                    "y0": float(top),
                    "x1": float(x1),
                    "y1": float(bottom),
                }
        except Exception:
            pass

        # Fallback: return dummy bbox
        return {"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 100.0}

    @staticmethod
    def _estimate_confidence(df: Any, num_rows: int, num_cols: int) -> float:
        """Estimate extraction confidence based on table characteristics.

        Args:
            df: Pandas DataFrame from Camelot
            num_rows: Number of rows
            num_cols: Number of columns

        Returns:
            Confidence score 0.0-1.0
        """
        score = 0.5

        # Penalize very small tables
        if num_rows < 2:
            score -= 0.2
        if num_cols < 2:
            score -= 0.2

        # Reward reasonable table sizes
        if 2 <= num_rows <= 100 and 2 <= num_cols <= 20:
            score += 0.2

        # Check for empty cells (suggest poor extraction)
        try:
            total_cells = num_rows * num_cols
            non_empty = sum(1 for row in df.values for cell in row if str(cell).strip())
            fill_ratio = non_empty / total_cells if total_cells > 0 else 0
            if fill_ratio < 0.3:
                score -= 0.3
            elif fill_ratio > 0.7:
                score += 0.1
        except Exception:
            pass

        return max(0.0, min(1.0, score))
