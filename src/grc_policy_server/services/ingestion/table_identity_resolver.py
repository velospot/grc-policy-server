"""Table identity resolver for detecting and linking split/continued tables.

Assigns stable UIDs to tables that are split across pages or have
continuation indicators, enabling accurate row-level comparison.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from grc_policy_server.services.ingestion.table_extraction_ensemble import TableCandidate

logger = logging.getLogger(__name__)

# Regex patterns for continuation detection
_CONTINUED_PATTERN = re.compile(
    r"\b(cont(?:inued)?|fortgesetzt|suite|următoare|continuazione|जारी)\b",
    re.IGNORECASE,
)
_PAGE_BREAK_PATTERN = re.compile(r"\(.*(?:page|seite|página|pagina|页).*\)", re.IGNORECASE)


def _normalize_text(text: str) -> str:
    """Simple text normalization for comparison."""
    # Convert to lowercase
    text = text.lower()
    # Remove extra whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Remove punctuation (keep alphanumeric and spaces)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return text


@dataclass(frozen=True)
class TableIdentity:
    """Stable identity information for a table."""

    table_uid: str
    caption_original: str
    caption_normalized: str
    pages: list[int]
    section_path: list[str]
    column_signature: str
    structure_hash: str
    content_hash: str
    is_split: bool = False
    continuation_signals: list[str] = None
    stitching_score: float = 0.0

    def __post_init__(self) -> None:
        """Validate identity data."""
        if not self.table_uid:
            raise ValueError("table_uid cannot be empty")
        if not self.pages:
            raise ValueError("pages must not be empty")


class TableIdentityResolver:
    """Resolves table identities and detects split/continued tables."""

    def __init__(
        self,
        caption_similarity_threshold: float = 0.7,
        structure_match_threshold: float = 0.85,
    ):
        """Initialize resolver.

        Args:
            caption_similarity_threshold: Jaccard similarity threshold for caption matching
            structure_match_threshold: Structure similarity threshold for table matching
        """
        self.caption_similarity_threshold = caption_similarity_threshold
        self.structure_match_threshold = structure_match_threshold

    def resolve_tables(
        self,
        candidates: list[TableCandidate],
        section_paths: dict[int, list[str]] | None = None,
    ) -> dict[str, TableIdentity]:
        """Resolve tables and detect split/continued instances.

        Args:
            candidates: List of table candidates from ensemble
            section_paths: Mapping of page number to section path list

        Returns:
            Mapping of table_uid to TableIdentity
        """
        if not candidates:
            return {}

        section_paths = section_paths or {}

        # Group candidates by similarity (same table, different pages)
        groups = self._group_candidates_by_similarity(candidates, section_paths)

        identities: dict[str, TableIdentity] = {}

        for group_idx, group in enumerate(groups):
            # Select best candidate from group as canonical
            canonical = self._select_canonical_candidate(group)

            # Check if this is a split table
            is_split = len(group) > 1 and self._is_split_continuation(group)

            # Generate stable UID
            table_uid = self._generate_table_uid(canonical, group_idx)

            # Collect pages from all group members
            all_pages = sorted(set(c.page_number for c in group))

            # Get section paths
            section_path = section_paths.get(canonical.page_number, [])

            # Compute hashes
            structure_hash = self._compute_structure_hash(canonical)
            content_hash = self._compute_content_hash(canonical)

            # Detect continuation signals
            signals = self._detect_continuation_signals(group, canonical)

            # Compute stitching score between first two group members (if split)
            stitching_score = 0.0
            if is_split and len(group) >= 2:
                sorted_group = sorted(group, key=lambda c: c.page_number)
                sp1 = section_paths.get(sorted_group[0].page_number, [])
                sp2 = section_paths.get(sorted_group[1].page_number, [])
                stitching_score = self._continuation_score(sorted_group[0], sorted_group[1], sp1, sp2)

            identity = TableIdentity(
                table_uid=table_uid,
                caption_original=canonical.metadata.get("caption_original", ""),
                caption_normalized=self._normalize_caption(
                    canonical.metadata.get("caption_original", "")
                ),
                pages=all_pages,
                section_path=section_path,
                column_signature=canonical.column_signature(),
                structure_hash=structure_hash,
                content_hash=content_hash,
                is_split=is_split,
                continuation_signals=signals,
                stitching_score=stitching_score,
            )

            identities[table_uid] = identity
            logger.debug(f"Resolved table: {table_uid} (pages {all_pages}, split={is_split})")

        return identities

    def _group_candidates_by_similarity(
        self,
        candidates: list[TableCandidate],
        section_paths: dict[int, list[str]] | None = None,
    ) -> list[list[TableCandidate]]:
        """Group candidates by similarity (same table on different pages/backends)."""
        if not candidates:
            return []

        section_paths = section_paths or {}

        # Sort by page number
        sorted_cands = sorted(candidates, key=lambda c: (c.page_number, c.bbox["x0"]))

        groups: list[list[TableCandidate]] = []
        assigned = set()

        for i, cand in enumerate(sorted_cands):
            if i in assigned:
                continue

            group = [cand]
            assigned.add(i)

            # Find similar candidates
            for j, other in enumerate(sorted_cands[i + 1 :], start=i + 1):
                if j in assigned:
                    continue

                # Check if candidates are similar (including section hierarchy)
                cand_section = section_paths.get(cand.page_number, [])
                other_section = section_paths.get(other.page_number, [])

                if self._are_candidates_similar(
                    cand, other, cand_section, other_section
                ):
                    group.append(other)
                    assigned.add(j)

            groups.append(group)

        return groups

    _CONTINUATION_THRESHOLD: float = 0.70

    def _continuation_score(
        self,
        cand1: TableCandidate,
        cand2: TableCandidate,
        section_path1: list[str] | None = None,
        section_path2: list[str] | None = None,
    ) -> float:
        """KB spec 6-factor continuation score for multi-page table stitching.

        Weights per KB doc 03:
        score = 0.20 * caption_sim
              + 0.35 * col_header_sim     (dominant signal — same schema = same table)
              + 0.10 * alignment_score    (x-position proximity)
              + 0.10 * repeated_header_score
              + 0.10 * adjacency_score
              + 0.15 * section_path_score (section context)
        """
        section_path1 = section_path1 or []
        section_path2 = section_path2 or []

        # Fail fast: different column count means cannot be continuation
        if cand1.num_cols != cand2.num_cols:
            return 0.0

        caption_sim = self._caption_similarity(cand1, cand2)
        col_header_sim = self._column_header_similarity(cand1, cand2)
        alignment = self._alignment_score(cand1, cand2)
        repeated_header = self._repeated_header_score(cand1, cand2)
        adjacency = self._adjacency_score(cand1, cand2)
        section_compat = 1.0 if self._section_hierarchies_compatible(
            section_path1, section_path2
        ) else 0.0

        return (
            0.20 * caption_sim
            + 0.35 * col_header_sim
            + 0.10 * alignment
            + 0.10 * repeated_header
            + 0.10 * adjacency
            + 0.15 * section_compat
        )

    def _are_candidates_similar(
        self,
        cand1: TableCandidate,
        cand2: TableCandidate,
        section_path1: list[str] | None = None,
        section_path2: list[str] | None = None,
    ) -> bool:
        """Check if two candidates represent the same (possibly multi-page) table.

        Uses the 6-factor KB spec continuation score with threshold 0.70.
        """
        score = self._continuation_score(cand1, cand2, section_path1, section_path2)
        return score >= self._CONTINUATION_THRESHOLD

    def _caption_similarity(self, cand1: TableCandidate, cand2: TableCandidate) -> float:
        """Normalized token Jaccard similarity between captions."""
        cap1 = self._normalize_caption(cand1.metadata.get("caption_original", ""))
        cap2 = self._normalize_caption(cand2.metadata.get("caption_original", ""))
        if not cap1 and not cap2:
            return 0.0  # No caption on either side: no signal (no reward, no penalty)
        if not cap1 or not cap2:
            return 0.3  # One-sided caption: weak match
        tokens1 = set(cap1.split())
        tokens2 = set(cap2.split())
        return self._jaccard_similarity(list(tokens1), list(tokens2))

    def _column_header_similarity(self, cand1: TableCandidate, cand2: TableCandidate) -> float:
        """Jaccard similarity between column signatures."""
        sig1 = cand1.column_signature()
        sig2 = cand2.column_signature()
        if not sig1 and not sig2:
            return 0.5
        if not sig1 or not sig2:
            return 0.2
        return self._jaccard_similarity(sig1.split("|"), sig2.split("|"))

    def _alignment_score(self, cand1: TableCandidate, cand2: TableCandidate) -> float:
        """X-position alignment score: linear decay from 0pt → 1.0 to 100pt → 0.0."""
        x_diff = abs(cand1.bbox.get("x0", 0) - cand2.bbox.get("x0", 0))
        if x_diff <= 0:
            return 1.0
        if x_diff >= 100:
            return 0.0
        return 1.0 - (x_diff / 100.0)

    def _repeated_header_score(self, cand1: TableCandidate, cand2: TableCandidate) -> float:
        """1.0 if the first data row of cand2 repeats cand1's headers (common on page breaks)."""
        if not cand1.headers or not cand2.cells:
            return 0.0
        # Get cand2's first row cells
        first_row_cells = [cell for cell in cand2.cells if cell.get("row", 999) == 0]
        if not first_row_cells:
            return 0.0
        first_row_texts = {
            _normalize_text(str(cell.get("text", "")))
            for cell in first_row_cells
            if cell.get("text")
        }
        header_texts = {_normalize_text(h) for h in cand1.headers if h}
        if not header_texts:
            return 0.0
        overlap = len(first_row_texts & header_texts)
        return overlap / len(header_texts) if header_texts else 0.0

    def _adjacency_score(self, cand1: TableCandidate, cand2: TableCandidate) -> float:
        """Page adjacency score: consecutive → 1.0, 1-page gap → 0.5, else → 0.0."""
        page_diff = abs(cand2.page_number - cand1.page_number)
        if page_diff == 1:
            return 1.0
        if page_diff == 2:
            return 0.5
        return 0.0

    def _section_hierarchies_compatible(
        self, path1: list[str], path2: list[str], min_depth: int = 2
    ) -> bool:
        """Check if two section paths are hierarchically compatible.

        Examples:
        - ["Kapitel 6", "6.1", "6.1.5"] vs ["Kapitel 6", "6.1", "6.1.5"] → True (exact)
        - ["Kapitel 6", "6.1", "6.1.5a"] vs ["Kapitel 6", "6.1", "6.1.5b"] → True (same prefix)
        - ["Kapitel 6", "6.1"] vs ["Kapitel 6", "6.2"] → False (different parent)
        """
        if not path1 or not path2:
            return True  # No path info = cannot reject

        # Extract section numbers for comparison
        nums1 = self._extract_section_numbers(path1)
        nums2 = self._extract_section_numbers(path2)

        if not nums1 or not nums2:
            return True  # Cannot judge without section numbers

        # Require at least min_depth levels for meaningful comparison
        if len(nums1) < min_depth or len(nums2) < min_depth:
            return True

        # Check common prefix (compare all but leaf level)
        common_depth = min(len(nums1), len(nums2)) - 1
        if common_depth < 1:
            return False

        for i in range(common_depth):
            if nums1[i] != nums2[i]:
                return False

        return True

    @staticmethod
    def _extract_section_numbers(section_path: list[str]) -> list[str]:
        """Extract section numbers from paths like ['6', '6.1', '6.1.5'].

        Returns list of section number strings like ['6', '6.1', '6.1.5'].
        """
        numbers = []
        for segment in section_path:
            # Extract leading numbers: "6.1.5 Messempfänger" → "6.1.5"
            match = re.match(r"^([\d\.]+)", segment)
            if match:
                numbers.append(match.group(1))
        return numbers

    def _is_split_continuation(self, group: list[TableCandidate]) -> bool:
        """Determine if group represents a split/continued table."""
        if len(group) < 2:
            return False

        # Sort by page
        sorted_group = sorted(group, key=lambda c: c.page_number)

        # Check for continuation signals
        for cand in sorted_group:
            signals = self._detect_continuation_signals([cand], cand)
            if signals:
                return True

        # Check for consecutive pages (typical split pattern)
        for i in range(len(sorted_group) - 1):
            page_diff = sorted_group[i + 1].page_number - sorted_group[i].page_number
            if page_diff <= 1:  # Consecutive or same page
                return True

        return False

    def _select_canonical_candidate(self, group: list[TableCandidate]) -> TableCandidate:
        """Select the canonical (best quality) candidate from a group."""
        if len(group) == 1:
            return group[0]

        # Prefer candidates with better headers and higher confidence
        def score(c: TableCandidate) -> tuple[float, int, int]:
            header_quality = len([h for h in c.headers if h and not str(h).startswith("column_")])
            num_cells = len(c.cells)
            return (c.confidence, header_quality, num_cells)

        return max(group, key=score)

    def _generate_table_uid(self, canonical: TableCandidate, group_idx: int) -> str:
        """Generate stable UID for a table.

        Format: TBL_{section}_{caption_normalized}_{group_idx}
        """
        caption = canonical.metadata.get("caption_original", f"table_{group_idx}")
        caption_norm = self._normalize_caption(caption)

        # Remove invalid characters for UID
        caption_norm = re.sub(r"[^a-z0-9_]", "_", caption_norm.lower())
        caption_norm = re.sub(r"_+", "_", caption_norm).strip("_")

        return f"tbl_{caption_norm}_{group_idx:03d}"

    def _normalize_caption(self, caption: str) -> str:
        """Normalize table caption for comparison."""
        # Remove continuation markers
        normalized = _CONTINUED_PATTERN.sub("", caption).strip()

        # Remove page break references
        normalized = _PAGE_BREAK_PATTERN.sub("", normalized).strip()

        # Apply standard text normalization
        normalized = _normalize_text(normalized)

        return normalized

    def _compute_structure_hash(self, candidate: TableCandidate) -> str:
        """Compute hash of table structure (headers, dimensions)."""
        structure = {
            "num_rows": candidate.num_rows,
            "num_cols": candidate.num_cols,
            "headers": candidate.headers,
            "column_signature": candidate.column_signature(),
        }

        import json

        structure_str = json.dumps(structure, sort_keys=True)
        return hashlib.sha256(structure_str.encode()).hexdigest()

    def _compute_content_hash(self, candidate: TableCandidate) -> str:
        """Compute hash of table content (cell text)."""
        cell_texts = []
        for cell in sorted(candidate.cells, key=lambda c: (c["row"], c["col"])):
            text = str(cell.get("text", "")).strip()
            if text:
                cell_texts.append(_normalize_text(text))

        content_str = "|".join(cell_texts)
        return hashlib.sha256(content_str.encode()).hexdigest()

    def _detect_continuation_signals(
        self,
        group: list[TableCandidate],
        canonical: TableCandidate,
    ) -> list[str]:
        """Detect continuation indicators in table."""
        signals: list[str] = []

        # Check caption for continuation markers
        caption = canonical.metadata.get("caption_original", "")
        if _CONTINUED_PATTERN.search(caption):
            signals.append("caption_continued_marker")

        # Check for "(continued)" in headers or first row
        for cell in canonical.cells:
            text = str(cell.get("text", "")).lower()
            if "(continued)" in text or "continued" in text:
                signals.append("content_continued_marker")
                break

        # Check for page boundary split
        if len(group) > 1:
            pages = sorted(set(c.page_number for c in group))
            if len(pages) > 1 and pages[-1] - pages[0] <= 2:
                signals.append("page_split")

        return signals

    @staticmethod
    def _jaccard_similarity(list1: list[str], list2: list[str]) -> float:
        """Compute Jaccard similarity between two lists."""
        if not list1 or not list2:
            return 0.0

        set1 = set(list1)
        set2 = set(list2)

        intersection = len(set1 & set2)
        union = len(set1 | set2)

        return intersection / union if union > 0 else 0.0
