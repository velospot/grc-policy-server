from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable

from grc_policy_server.services.comparison.policy_semantics import (
    ClauseMeaning,
    clean_policy_text,
    compare_clause_meaning,
    extract_clause_meaning,
    is_non_semantic_content,
    semantic_signature,
    token_overlap,
)
from grc_policy_server.services.documents.canonical_models import (
    COMPARISON_NODE_TYPES,
    TEXT_COMPARISON_NODE_TYPES,
)
from grc_policy_server.utils.hashing import normalize_whitespace


@dataclass(frozen=True)
class MatchThresholds:
    max_match_distance: float = 0.35
    unchanged_distance: float = 0.20
    modified_distance: float = 0.25
    min_section_score: float = 0.45
    min_clause_score: float = 0.50


@dataclass(frozen=True)
class ClauseMatch:
    distance: float
    matched_by: str
    left: dict
    right: dict


@dataclass(frozen=True)
class ClauseMatchingResult:
    matches: list[ClauseMatch]
    removed: list[dict]
    added: list[dict]
    section_matches: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class _SectionBucket:
    key: str
    stable_id: str
    title: str
    order: int
    clean_text: str
    items: list[dict]


SearchFn = Callable[..., list[dict]]


class ClauseMatcher:
    def __init__(
        self,
        *,
        search_fn: SearchFn,
        thresholds: MatchThresholds,
        topk: int = 5,
        language: str = "",
    ) -> None:
        self.search_fn = search_fn
        self.thresholds = thresholds
        self.topk = topk
        self.language = language

    def match(
        self,
        *,
        left_nodes: list[dict],
        right_nodes: list[dict],
        target_document_id: str,
    ) -> ClauseMatchingResult:
        left = self._select_content_nodes(left_nodes)
        right = self._select_content_nodes(right_nodes)
        right_by_id = {str(node.get("chunk_id") or ""): node for node in right}
        matched_left: dict[str, ClauseMatch] = {}
        matched_right_ids: set[str] = set()

        left_sections = self._build_sections(left_nodes, left)
        right_sections = self._build_sections(right_nodes, right)
        matched_section_keys = self._match_sections(left_sections, right_sections)
        section_map = {left: right for left, right, _ in matched_section_keys}

        for left_key, right_key, matched_by in matched_section_keys:
            self._match_section_items(
                left_sections[left_key].items,
                right_sections[right_key].items,
                matched_left=matched_left,
                matched_right_ids=matched_right_ids,
                matched_by=matched_by,
            )

        self._vector_fallback(
            left_nodes=left,
            right_by_id=right_by_id,
            matched_left=matched_left,
            matched_right_ids=matched_right_ids,
            target_document_id=target_document_id,
            section_map=section_map,
        )

        removed = [
            node for node in left if str(node.get("chunk_id") or "") not in matched_left
        ]
        added = [
            node
            for node in right
            if str(node.get("chunk_id") or "") not in matched_right_ids
        ]
        return ClauseMatchingResult(
            matches=list(matched_left.values()),
            removed=removed,
            added=added,
            section_matches=[(left, right) for left, right, _ in matched_section_keys],
        )

    def _select_content_nodes(self, nodes: list[dict]) -> list[dict]:
        content_nodes = [
            node
            for node in nodes
            if node.get("node_type") in COMPARISON_NODE_TYPES
            and not is_non_semantic_content(self._node_clean_text(node))
        ]
        if content_nodes:
            prioritized = [node for node in content_nodes if not node.get("low_priority")]
            if prioritized:
                return sorted(prioritized, key=self._node_sort_key)
            return sorted(content_nodes, key=self._node_sort_key)
        clause_nodes = [
            node
            for node in nodes
            if node.get("node_type") in COMPARISON_NODE_TYPES
            and not is_non_semantic_content(self._node_clean_text(node))
        ]
        if clause_nodes:
            return sorted(clause_nodes, key=self._node_sort_key)
        return sorted(nodes, key=self._node_sort_key)

    def _build_sections(
        self, all_nodes: list[dict], content_nodes: list[dict]
    ) -> dict[str, _SectionBucket]:
        section_nodes = {
            str(node.get("section_path") or "Unknown Section"): node
            for node in all_nodes
            if node.get("node_type") == "section"
        }
        grouped_items: dict[str, list[dict]] = defaultdict(list)
        for node in content_nodes:
            grouped_items[str(node.get("section_path") or "Unknown Section")].append(
                node
            )

        buckets: dict[str, _SectionBucket] = {}
        for order, section_path in enumerate(
            sorted(
                grouped_items,
                key=lambda path: self._section_sort_key(grouped_items[path]),
            )
        ):
            items = sorted(grouped_items[section_path], key=self._node_sort_key)
            section_node = section_nodes.get(section_path)
            stable_id = str(
                (section_node or {}).get("stable_id")
                or (section_node or {}).get("section_path")
                or section_path
            )
            title = str(
                (section_node or {}).get("title") or section_path.split(" / ")[-1]
            )
            clean_text = str(
                (section_node or {}).get("section_summary")
                or (section_node or {}).get("summary_text")
                or (section_node or {}).get("clean_text")
                or ""
            ).strip()
            if not clean_text:
                clean_text = " ".join(
                    self._node_clean_text(item) for item in items if item
                )
            buckets[section_path] = _SectionBucket(
                key=section_path,
                stable_id=stable_id,
                title=title,
                order=order,
                clean_text=clean_text,
                items=items,
            )
        return buckets

    def _match_sections(
        self,
        left_sections: dict[str, _SectionBucket],
        right_sections: dict[str, _SectionBucket],
    ) -> list[tuple[str, str, str]]:
        matches: list[tuple[str, str, str]] = []
        matched_left: set[str] = set()
        matched_right: set[str] = set()

        left_by_stable = Counter(
            section.stable_id for section in left_sections.values()
        )
        right_by_stable = Counter(
            section.stable_id for section in right_sections.values()
        )
        unique_right = defaultdict(list)
        for key, section in right_sections.items():
            unique_right[section.stable_id].append(key)

        for left_key, left_section in left_sections.items():
            if (
                left_by_stable[left_section.stable_id] == 1
                and right_by_stable[left_section.stable_id] == 1
            ):
                right_key = unique_right[left_section.stable_id][0]
                matches.append((left_key, right_key, "section_stable_id"))
                matched_left.add(left_key)
                matched_right.add(right_key)

        candidate_edges: list[tuple[float, str, str]] = []
        for left_key, left_section in left_sections.items():
            if left_key in matched_left:
                continue
            for right_key, right_section in right_sections.items():
                if right_key in matched_right:
                    continue
                score = self._section_score(left_section, right_section)
                if score >= self.thresholds.min_section_score:
                    candidate_edges.append((score, left_key, right_key))

        candidate_edges.sort(key=lambda item: item[0], reverse=True)
        for score, left_key, right_key in candidate_edges:
            if left_key in matched_left or right_key in matched_right:
                continue
            matches.append((left_key, right_key, "section_alignment"))
            matched_left.add(left_key)
            matched_right.add(right_key)

        return matches

    def _match_section_items(
        self,
        left_items: list[dict],
        right_items: list[dict],
        *,
        matched_left: dict[str, ClauseMatch],
        matched_right_ids: set[str],
        matched_by: str,
    ) -> None:
        left_by_stable = Counter(
            str(node.get("stable_id") or "")
            for node in left_items
            if node.get("stable_id")
        )
        right_by_stable: dict[str, list[dict]] = defaultdict(list)
        for node in right_items:
            stable_id = str(node.get("stable_id") or "")
            if stable_id:
                right_by_stable[stable_id].append(node)

        for left_node in left_items:
            left_id = str(left_node.get("chunk_id") or "")
            stable_id = str(left_node.get("stable_id") or "")
            if not left_id or left_id in matched_left or not stable_id:
                continue
            if (
                left_by_stable[stable_id] != 1
                or len(right_by_stable.get(stable_id, [])) != 1
            ):
                continue
            right_node = right_by_stable[stable_id][0]
            right_id = str(right_node.get("chunk_id") or "")
            if right_id in matched_right_ids:
                continue
            distance = 1.0 - self._clause_score(left_node, right_node)
            matched_left[left_id] = ClauseMatch(
                distance=distance,
                matched_by="stable_id",
                left=left_node,
                right=right_node,
            )
            matched_right_ids.add(right_id)

        candidate_edges: list[tuple[float, str, str, dict, dict]] = []
        for left_node in left_items:
            left_id = str(left_node.get("chunk_id") or "")
            if not left_id or left_id in matched_left:
                continue
            for right_node in right_items:
                right_id = str(right_node.get("chunk_id") or "")
                if not right_id or right_id in matched_right_ids:
                    continue
                score = self._clause_score(left_node, right_node)
                if score >= self.thresholds.min_clause_score:
                    candidate_edges.append(
                        (score, left_id, right_id, left_node, right_node)
                    )

        candidate_edges.sort(key=lambda item: item[0], reverse=True)
        for score, left_id, right_id, left_node, right_node in candidate_edges:
            if left_id in matched_left or right_id in matched_right_ids:
                continue
            distance = 1.0 - score
            if distance > self.thresholds.max_match_distance:
                continue
            matched_left[left_id] = ClauseMatch(
                distance=distance,
                matched_by=matched_by,
                left=left_node,
                right=right_node,
            )
            matched_right_ids.add(right_id)

        # When a section pair has a single unmatched item on each side, keep them aligned
        # even if lexical distance is slightly above the global threshold.
        unmatched_left = [
            node
            for node in left_items
            if str(node.get("chunk_id") or "") not in matched_left
        ]
        unmatched_right = [
            node
            for node in right_items
            if str(node.get("chunk_id") or "") not in matched_right_ids
        ]
        if len(unmatched_left) == 1 and len(unmatched_right) == 1:
            left_node = unmatched_left[0]
            right_node = unmatched_right[0]
            left_id = str(left_node.get("chunk_id") or "")
            right_id = str(right_node.get("chunk_id") or "")
            if (
                left_id
                and right_id
                and left_id not in matched_left
                and right_id not in matched_right_ids
                and left_node.get("node_type") == right_node.get("node_type")
            ):
                score = self._clause_score(left_node, right_node)
                matched_left[left_id] = ClauseMatch(
                    distance=1.0 - score,
                    matched_by=matched_by,
                    left=left_node,
                    right=right_node,
                )
                matched_right_ids.add(right_id)

    def _vector_fallback(
        self,
        *,
        left_nodes: list[dict],
        right_by_id: dict[str, dict],
        matched_left: dict[str, ClauseMatch],
        matched_right_ids: set[str],
        target_document_id: str,
        section_map: dict[str, str],
    ) -> None:
        node_types = sorted(
            {
                str(node.get("node_type") or "clause")
                for node_id, node in right_by_id.items()
                if node_id not in matched_right_ids
            }
        )
        if not node_types:
            return
        search_node_types = set(node_types)
        if search_node_types & TEXT_COMPARISON_NODE_TYPES:
            search_node_types.add("clause")

        candidate_edges: list[tuple[float, str, str, dict, dict]] = []
        for left_node in left_nodes:
            left_id = str(left_node.get("chunk_id") or "")
            if not left_id or left_id in matched_left:
                continue
            left_section = str(left_node.get("section_path") or "")
            right_section = section_map.get(left_section)
            if not right_section:
                # Hierarchical compare: only align within matched sections.
                continue
            query_text = self._node_clean_text(left_node)
            if not query_text:
                continue
            left_node_type = str(left_node.get("node_type") or "clause")
            if left_node_type == "table":
                query_node_types = ["table"]
            else:
                # text nodes must not pull in table results
                non_table_types = sorted(search_node_types - {"table"})
                query_node_types = non_table_types or sorted(search_node_types)
            matches = self.search_fn(
                query_string=str(left_node.get("section_path") or ""),
                query_text=query_text,
                target_document_id=target_document_id,
                limit=self.topk,
                node_types=query_node_types,
            )
            for candidate in matches:
                right_id = str(candidate.get("chunk_id") or "")
                if str(candidate.get("section_path") or "") != right_section:
                    continue
                if (
                    not right_id
                    or right_id in matched_right_ids
                    or right_id not in right_by_id
                ):
                    continue
                right_node = right_by_id[right_id]
                score = self._clause_score(left_node, right_node)
                if score < self.thresholds.min_clause_score:
                    continue
                candidate_edges.append(
                    (score, left_id, right_id, left_node, right_node)
                )

        candidate_edges.sort(key=lambda item: item[0], reverse=True)
        for score, left_id, right_id, left_node, right_node in candidate_edges:
            if left_id in matched_left or right_id in matched_right_ids:
                continue
            distance = 1.0 - score
            if distance > self.thresholds.max_match_distance:
                continue
            matched_left[left_id] = ClauseMatch(
                distance=distance,
                matched_by="vector_search",
                left=left_node,
                right=right_node,
            )
            matched_right_ids.add(right_id)

    # Matches leading section numbers like "3.", "3.1", "3.1.2", "A.1", "B.2.1" or
    # keyword prefixes like "Section 4", "Article 2.1", "Annex A.1", "Chapter 3 -"
    _SECTION_NUMBER_RE = re.compile(
        r'^(?:(?:section|article|chapter|part|annex|appendix)\s+)?(?:[A-Z]\.)?[\d.]+\s*[-–:.]?\s*',
        re.IGNORECASE,
    )
    # Extracts the pure numeric portion of a section number (e.g. "3.2.1" from "3.2.1 Title")
    _SECTION_NUM_EXTRACT_RE = re.compile(r'^(?:[A-Z]\.)?(\d+(?:\.\d+)*)', re.IGNORECASE)
    # Detects a table/figure caption row: starts with "table N" or "figure N"
    _CAPTION_ROW_RE = re.compile(r'^(?:table|tbl\.?|figure|fig\.?)\s*\d', re.IGNORECASE)

    def _normalize_section_title(self, title: str) -> str:
        """Strip leading section numbers/keywords so titles like
        '3.1 Data Protection' and '4.1 Data Protection' compare as equal."""
        cleaned = self._SECTION_NUMBER_RE.sub('', title).strip()
        return cleaned or title

    def _numbering_depth_score(self, left_key: str, right_key: str) -> float:
        """Score how closely the section numbering depths match.

        Sections at the same nesting depth (e.g. "3.2" and "8.2") receive 1.0.
        Sections differing by one level receive 0.6; more than one level, 0.2.
        Unnumbered sections on both sides receive 1.0.
        """
        left_m = self._SECTION_NUM_EXTRACT_RE.match(left_key.strip())
        right_m = self._SECTION_NUM_EXTRACT_RE.match(right_key.strip())
        if not left_m and not right_m:
            return 1.0  # both unnumbered (e.g. "FOREWORD")
        if not left_m or not right_m:
            return 0.3  # one numbered, one not
        left_depth = left_m.group(1).count('.') + 1
        right_depth = right_m.group(1).count('.') + 1
        diff = abs(left_depth - right_depth)
        if diff == 0:
            return 1.0
        if diff == 1:
            return 0.6
        return 0.2

    def _has_caption_row(self, node: dict) -> bool:
        """Return True when a table's row 0 is a caption embedded as a table cell.

        This happens when the PDF extractor folds the caption text into the first
        row of the table rather than exposing it as a separate title field.
        A caption row is identified by:
          - a single cell at (row=0, col=0) that spans all (or all-but-one) columns, AND
          - the cell text matches the "Table N" / "Figure N" pattern.
        """
        cells = node.get("table_cells") or []
        num_cols = int(node.get("table_num_cols") or 0)
        if not cells or num_cols == 0:
            return False
        row0_cells = [c for c in cells if int(c.get("row", -1)) == 0]
        if len(row0_cells) != 1:
            return False
        cell = row0_cells[0]
        col_span = int(cell.get("col_span", 1))
        text = str(cell.get("text") or "").strip()
        return col_span >= max(1, num_cols - 1) and bool(self._CAPTION_ROW_RE.match(text))

    def _normalize_cells_for_comparison(self, node: dict) -> list[dict]:
        """Return cells with the caption row stripped and remaining rows re-indexed.

        When a table has a caption at row 0, stripping it and shifting all other
        rows down by 1 aligns the cell grid with a table that stores the caption
        externally, enabling accurate position-based cell comparison.
        """
        cells = node.get("table_cells") or []
        if not cells or not self._has_caption_row(node):
            return cells
        result = []
        for c in cells:
            row = int(c.get("row", 0))
            if row == 0:
                continue
            result.append({**c, "row": row - 1})
        return result

    def _section_score(self, left: _SectionBucket, right: _SectionBucket) -> float:
        """Hybrid section similarity using four weighted components.

        Formula (mirrors the spec):
          0.20 * title_similarity       – normalized title match
          0.15 * numbering_similarity   – section depth match
          0.15 * path_similarity        – document-order proximity (proxy for parent path)
          0.50 * content_similarity     – token overlap of concatenated child content

        Content gets the dominant weight so sections that were renumbered or
        lightly renamed still align as long as their child content is similar.
        Order proximity uses a soft penalty (× 0.3) so sections that shifted
        position across versions are not penalised too harshly.
        """
        left_title = self._normalize_section_title(left.title.lower())
        right_title = self._normalize_section_title(right.title.lower())
        title_score = SequenceMatcher(None, left_title, right_title).ratio()
        content_score = token_overlap(left.clean_text, right.clean_text, self.language)
        numbering_score = self._numbering_depth_score(left.key, right.key)
        order_penalty = abs(left.order - right.order)
        path_score = 1.0 / (1 + order_penalty * 0.3)
        return (
            0.20 * title_score
            + 0.15 * numbering_score
            + 0.15 * path_score
            + 0.50 * content_score
        )

    def _clause_score(self, left: dict, right: dict) -> float:
        # Use table-specific scoring for tables
        if left.get("node_type") == "table" and right.get("node_type") == "table":
            return self._table_score(left, right)

        left_text = self._node_clean_text(left)
        right_text = self._node_clean_text(right)
        if not left_text and not right_text:
            return 1.0
        if not left_text or not right_text:
            return 0.0

        normalized_left = normalize_whitespace(left_text)
        normalized_right = normalize_whitespace(right_text)

        text_score = SequenceMatcher(None, normalized_left, normalized_right).ratio()
        lexical_score = token_overlap(left_text, right_text, self.language)
        length_score = self._length_similarity(left_text, right_text)
        meaning_score = self._meaning_score(left, right)
        signature_score = token_overlap(
            self._semantic_signature_from_node(left),
            self._semantic_signature_from_node(right),
            self.language,
        )

        # Weight text-based scores higher for robustness with numerical changes
        # Text similarity is deterministic; semantic extraction can vary
        score = (
            0.35 * text_score
            + 0.20 * lexical_score
            + 0.05 * length_score
            + 0.25 * meaning_score
            + 0.15 * signature_score
        )
        if left.get("node_type") != right.get("node_type"):
            score *= 0.8
        return max(0.0, min(score, 1.0))

    def _table_title_score(self, left: dict, right: dict) -> float | None:
        """Similarity between table captions/titles after normalisation.

        Prefers the pre-normalised ``table_normalized_caption`` stored at
        ingestion time (which already strips "Table N:", "Figure 3 -", etc.)
        and falls back to stripping section-number prefixes from the raw title.

        Returns None when either table has no caption/title so the caller can
        skip this component rather than treating absence as zero similarity.
        """
        left_title = (
            str(left.get("table_normalized_caption") or "").strip()
            or self._normalize_section_title(str(left.get("title") or "").lower()).strip()
        )
        right_title = (
            str(right.get("table_normalized_caption") or "").strip()
            or self._normalize_section_title(str(right.get("title") or "").lower()).strip()
        )
        if not left_title or not right_title:
            return None
        return SequenceMatcher(None, left_title, right_title).ratio()

    def _table_score(self, left: dict, right: dict) -> float:
        """Compute similarity score for tables.

        Matching is a two-step process that mirrors the overall comparison
        strategy: first check whether the tables *refer to the same subject*
        (title similarity), then measure how much of the actual content
        (cells, structure) changed.

        Caption-row normalisation: some PDF extractors embed the table caption
        as a spanning row 0 rather than as an external title.  We detect and
        strip such rows before comparing cell grids and dimensions so that a
        table with an embedded caption is treated identically to one where the
        caption is stored externally.

        When tables have different section numbers or depths the title
        normalization strips the numeric prefix so "Table 3: Risk Matrix" and
        "Table 5: Risk Matrix" still receive a high title score.
        """
        # Normalise cells – strip caption rows and re-index remaining rows.
        left_cells_norm = self._normalize_cells_for_comparison(left)
        right_cells_norm = self._normalize_cells_for_comparison(right)
        left_has_caption = self._has_caption_row(left)
        right_has_caption = self._has_caption_row(right)

        # Use caption-adjusted row counts for dimension comparison.
        left_rows = max(0, (left.get("table_num_rows") or 0) - (1 if left_has_caption else 0))
        left_cols = left.get("table_num_cols", 0)
        right_rows = max(0, (right.get("table_num_rows") or 0) - (1 if right_has_caption else 0))
        right_cols = right.get("table_num_cols", 0)

        left_schema = str(left.get("table_schema_signature") or "")
        right_schema = str(right.get("table_schema_signature") or "")
        left_row_fp = set(left.get("table_row_fingerprints") or [])
        right_row_fp = set(right.get("table_row_fingerprints") or [])

        # Step 1 – title similarity (subject-level match).
        # Prefer the table's own normalised caption; fall back to any caption row
        # text that was detected during normalisation.
        title_score = self._table_title_score(left, right)
        if title_score is None and (left_has_caption or right_has_caption):
            # Derive title from the caption row cell text.
            def _caption_text(node: dict) -> str:
                for c in (node.get("table_cells") or []):
                    if int(c.get("row", -1)) == 0 and int(c.get("col", -1)) == 0:
                        return self._normalize_section_title(str(c.get("text") or "").lower()).strip()
                return ""
            lt = _caption_text(left)
            rt = _caption_text(right)
            if lt and rt:
                title_score = SequenceMatcher(None, lt, rt).ratio()
        has_title = title_score is not None

        schema_score = 0.0
        if left_schema and right_schema:
            schema_score = 1.0 if left_schema == right_schema else 0.0

        row_fp_score = 0.0
        if left_row_fp and right_row_fp:
            row_fp_score = len(left_row_fp & right_row_fp) / len(left_row_fp | right_row_fp)

        # Step 2 – content similarity (cell / text / structure).
        # If no structural cell data fall back to full-text comparison.
        if not left_cells_norm or not right_cells_norm:
            text_score = self._table_text_score(left, right)
            if has_title:
                if title_score >= 0.85:  # type: ignore[operator]
                    return (
                        0.50 * title_score  # type: ignore[operator]
                        + 0.35 * text_score
                        + 0.10 * schema_score
                        + 0.05 * row_fp_score
                    )
                return (
                    0.35 * title_score  # type: ignore[operator]
                    + 0.40 * text_score
                    + 0.15 * schema_score
                    + 0.10 * row_fp_score
                )
            return 0.60 * text_score + 0.25 * schema_score + 0.15 * row_fp_score

        # Dimension similarity (using caption-adjusted counts).
        if left_rows == right_rows and left_cols == right_cols:
            dim_score = 1.0
        else:
            row_sim = min(left_rows, right_rows) / max(left_rows, right_rows, 1)
            col_sim = min(left_cols, right_cols) / max(left_cols, right_cols, 1)
            dim_score = (row_sim + col_sim) / 2

        # Cell content comparison using normalised cells (caption rows removed).
        # Skip cells that carry no semantic value (page numbers, bare numerics).
        # Strip trailing punctuation so "Temperature:" == "temperature".
        def _norm_cell(text: str) -> str:
            t = str(text).lower().strip()
            return t.rstrip(':.,;-')

        left_cell_map = {
            (c.get("row", 0), c.get("col", 0)): _norm_cell(str(c.get("text", "")))
            for c in left_cells_norm
            if not is_non_semantic_content(str(c.get("text", "")))
        }
        right_cell_map = {
            (c.get("row", 0), c.get("col", 0)): _norm_cell(str(c.get("text", "")))
            for c in right_cells_norm
            if not is_non_semantic_content(str(c.get("text", "")))
        }

        all_positions = set(left_cell_map.keys()) | set(right_cell_map.keys())
        if not all_positions:
            cell_score = dim_score
        else:
            exact_matches = sum(
                1
                for pos in all_positions
                if left_cell_map.get(pos) == right_cell_map.get(pos)
                and left_cell_map.get(pos)  # Non-empty match
            )
            partial_matches = 0.0
            for pos in all_positions:
                left_val = left_cell_map.get(pos, "")
                right_val = right_cell_map.get(pos, "")
                if left_val and right_val and left_val != right_val:
                    overlap = token_overlap(left_val, right_val, self.language)
                    if overlap > 0.5:
                        partial_matches += overlap

            cell_score = (exact_matches + partial_matches) / len(all_positions)

        text_score = self._table_text_score(left, right)

        if has_title:
            if title_score >= 0.85:  # type: ignore[operator]
                return (
                    0.40 * title_score  # type: ignore[operator]
                    + 0.30 * cell_score
                    + 0.10 * dim_score
                    + 0.08 * text_score
                    + 0.07 * schema_score
                    + 0.05 * row_fp_score
                )
            return (
                0.20 * title_score  # type: ignore[operator]
                + 0.35 * cell_score
                + 0.15 * dim_score
                + 0.10 * text_score
                + 0.10 * schema_score
                + 0.10 * row_fp_score
            )

        # No title available – fall back to structure-only weights.
        return (
            0.20 * dim_score
            + 0.45 * cell_score
            + 0.15 * text_score
            + 0.10 * schema_score
            + 0.10 * row_fp_score
        )

    def _table_text_score(self, left: dict, right: dict) -> float:
        """Compute text-based similarity for tables (fallback when no structure)."""
        # Prefer markdown_text for tables as it preserves structure
        left_text = str(
            left.get("markdown_text")
            or left.get("clean_text")
            or left.get("text")
            or ""
        )
        right_text = str(
            right.get("markdown_text")
            or right.get("clean_text")
            or right.get("text")
            or ""
        )

        if not left_text and not right_text:
            return 1.0
        if not left_text or not right_text:
            return 0.0

        # Use SequenceMatcher for overall structure similarity
        text_ratio = SequenceMatcher(
            None, normalize_whitespace(left_text), normalize_whitespace(right_text)
        ).ratio()

        # Token overlap for content similarity
        lexical_score = token_overlap(left_text, right_text, self.language)

        return 0.5 * text_ratio + 0.5 * lexical_score

    def _meaning_score(self, left: dict, right: dict) -> float:
        comparison = compare_clause_meaning(
            self._node_meaning(left),
            self._node_meaning(right),
            self.language,
        )
        score = comparison.score
        if (
            comparison.obligation_change in {"strengthened", "weakened"}
            and score >= 0.35
        ):
            return min(score + 0.15, 1.0)
        return score

    def _node_meaning(self, node: dict) -> ClauseMeaning:
        obligation = str(node.get("obligation") or "")
        subject = str(node.get("subject") or "")
        action = str(node.get("action") or "")
        obj = str(node.get("object") or "")
        condition = str(node.get("condition") or "")
        if obligation or subject or action or obj or condition:
            return ClauseMeaning(obligation, subject, action, obj, condition)
        return extract_clause_meaning(str(node.get("text") or ""))

    def _semantic_signature_from_node(self, node: dict) -> str:
        if any(
            node.get(field) for field in ("subject", "action", "object", "condition")
        ):
            return " | ".join(
                str(node.get(field) or "")
                for field in ("subject", "action", "object", "condition")
                if node.get(field)
            )
        return semantic_signature(str(node.get("text") or ""))

    def _node_clean_text(self, node: dict) -> str:
        return str(
            node.get("comparison_text")
            or node.get("canonical_text")
            or node.get("clean_text")
            or clean_policy_text(str(node.get("text") or ""))
        ).strip()

    def _length_similarity(self, left: str, right: str) -> float:
        longest = max(len(left), len(right))
        if longest == 0:
            return 1.0
        return min(len(left), len(right)) / longest

    def _section_sort_key(self, items: list[dict]) -> tuple[int, int]:
        if not items:
            return (10**9, 10**9)
        return self._node_sort_key(items[0])

    def _node_sort_key(self, node: dict) -> tuple[int, int]:
        page = node.get("page_number")
        if page is None:
            page = node.get("page")
        chunk_index = node.get("chunk_index")
        if chunk_index is None:
            chunk_index = 0
        return (int(page or 0), int(chunk_index or 0))
