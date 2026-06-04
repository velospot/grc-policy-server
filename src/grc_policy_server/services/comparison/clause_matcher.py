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

_NUMBER_EXTRACT_RE = re.compile(
    r'\b\d+(?:[.,]\d+)?\s*(?:v/m|mv/m|khz|mhz|ghz|hz|dbuv|dbua|v|a|%|ms|s|m|cm|mm)?\b',
    re.IGNORECASE,
)
_EMC_ENTITY_RE = re.compile(
    r'\b(?:class\s*[a-e]|performance\s*criterion\s*[a-e]'
    r'|iec\s*\d+[-\s.]\d+|cispr\s*\d+|iso\s*\d+[-\s.]\d+'
    r'|radiated|conducted|esd|immunity|emission)\b',
    re.IGNORECASE,
)


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
        search_fn: SearchFn | None = None,
        thresholds: MatchThresholds,
        topk: int = 5,
        language: str = "",
    ) -> None:
        self.search_fn = search_fn
        self.thresholds = thresholds
        self.topk = topk
        self.language = language
        if not self.__class__._FOLDED_SYNONYMS:
            self.__class__._build_folded_synonyms()

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

        self._resolve_split_table_pairs(
            left_nodes=left,
            right_nodes=right,
            matched_left=matched_left,
            matched_right_ids=matched_right_ids,
        )

        unmatched_left_nodes = [
            node for node in left if str(node.get("chunk_id") or "") not in matched_left
        ]
        unmatched_right_nodes = [
            node
            for node in right
            if str(node.get("chunk_id") or "") not in matched_right_ids
        ]

        # Detect section-level renaming: groups of REMOVED nodes whose aggregated
        # content closely mirrors a group of ADDED nodes (cross-chapter renumbering).
        renamed_pairs = self._detect_section_renaming(
            unmatched_left_nodes, unmatched_right_nodes
        )
        for left_group, right_group in renamed_pairs:
            for node in left_group:
                nid = str(node.get("chunk_id") or "")
                if nid and nid not in matched_left:
                    matched_left[nid] = ClauseMatch(
                        distance=0.15,
                        matched_by="section_renamed",
                        left=node,
                        right=right_group[0] if right_group else node,
                    )
            for node in right_group:
                matched_right_ids.add(str(node.get("chunk_id") or ""))

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

    def _detect_section_renaming(
        self,
        unmatched_left: list[dict],
        unmatched_right: list[dict],
        min_overlap: float = 0.55,
        min_group_size: int = 2,
    ) -> list[tuple[list[dict], list[dict]]]:
        """Detect when an entire section was renumbered between document versions.

        Groups unmatched nodes by their top-level chapter key.  When a left chapter
        group and a right chapter group have high token overlap (≥ min_overlap) and
        contain at least min_group_size nodes, classify the pair as section_renamed
        rather than independent REMOVED + ADDED records.

        Returns a list of (left_group, right_group) node-list pairs.
        """
        def _group_by_chapter(nodes: list[dict]) -> dict[str, list[dict]]:
            groups: dict[str, list[dict]] = defaultdict(list)
            for node in nodes:
                sp = str(node.get("section_path") or "")
                groups[self._chapter_key(sp)].append(node)
            return groups

        def _agg_text(nodes: list[dict]) -> str:
            return " ".join(
                str(n.get("clean_text") or n.get("text") or "") for n in nodes
            )

        left_groups = _group_by_chapter(unmatched_left)
        right_groups = _group_by_chapter(unmatched_right)

        rename_edges: list[tuple[float, str, str]] = []
        for lch, lnodes in left_groups.items():
            if len(lnodes) < min_group_size:
                continue
            lagg = _agg_text(lnodes)
            if not lagg:
                continue
            for rch, rnodes in right_groups.items():
                if rch == lch or len(rnodes) < min_group_size:
                    continue
                ragg = _agg_text(rnodes)
                if not ragg:
                    continue
                score = token_overlap(lagg, ragg, self.language)
                if score >= min_overlap:
                    rename_edges.append((score, lch, rch))

        rename_edges.sort(key=lambda e: e[0], reverse=True)
        pairs: list[tuple[list[dict], list[dict]]] = []
        used_left: set[str] = set()
        used_right: set[str] = set()
        for _score, lch, rch in rename_edges:
            if lch in used_left or rch in used_right:
                continue
            pairs.append((left_groups[lch], right_groups[rch]))
            used_left.add(lch)
            used_right.add(rch)

        return pairs

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
            _stl = list((section_node or {}).get("section_titles") or [])
            title = str(
                (section_node or {}).get("title")
                or (_stl[-1] if _stl else None)
                or section_path
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

    @staticmethod
    def _chapter_key(section_path: str) -> str:
        """Extract the top-level chapter identifier from a section path.

        "14.4.3 Test results" → "14"
        "8.3.7 Test result"   → "8"
        "Foreword"            → "Foreword"
        """
        m = re.match(r"^(\d+)(?:\.\d+)*", section_path.strip())
        return m.group(1) if m else section_path.strip()

    def _match_chapters_by_content(
        self,
        left_sections: dict[str, _SectionBucket],
        right_sections: dict[str, _SectionBucket],
        matched_left: set[str],
        matched_right: set[str],
    ) -> dict[str, str]:
        """Build a chapter-level correspondence map between left and right documents.

        Groups all sections by their top-level chapter key, then scores chapter
        pairs by aggregated content similarity.  Returns a mapping from left chapter
        key to right chapter key for pairs whose content overlap exceeds 0.35.

        This enables the section scorer to give a bonus to sections whose chapters
        are already matched (e.g. chapter "14" in 2016 → chapter "8" in 2019).
        """
        def _agg(sections: dict[str, _SectionBucket], chapter: str) -> str:
            parts = [
                s.clean_text
                for key, s in sections.items()
                if self._chapter_key(key) == chapter
            ]
            return " ".join(p for p in parts if p)

        # Gather distinct chapter keys for unmatched sections on each side
        left_chapters: set[str] = set()
        right_chapters: set[str] = set()
        for key in left_sections:
            if key not in matched_left:
                left_chapters.add(self._chapter_key(key))
        for key in right_sections:
            if key not in matched_right:
                right_chapters.add(self._chapter_key(key))

        # Score all chapter pairs by aggregated content token overlap
        chapter_edges: list[tuple[float, str, str]] = []
        for lch in left_chapters:
            lagg = _agg(left_sections, lch)
            if not lagg:
                continue
            for rch in right_chapters:
                ragg = _agg(right_sections, rch)
                if not ragg:
                    continue
                score = token_overlap(lagg, ragg, self.language)
                if score >= 0.35:
                    chapter_edges.append((score, lch, rch))

        chapter_edges.sort(key=lambda e: e[0], reverse=True)
        chapter_map: dict[str, str] = {}
        mapped_right: set[str] = set()
        for score, lch, rch in chapter_edges:
            if lch in chapter_map or rch in mapped_right:
                continue
            # Only record cross-chapter mappings (same numbers are handled by title score)
            if lch != rch:
                chapter_map[lch] = rch
            mapped_right.add(rch)

        return chapter_map

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

        # Build chapter-level correspondence map so cross-chapter renumbering
        # (e.g. DNVGL chapter 14 → chapter 8) can still yield section matches.
        chapter_map = self._match_chapters_by_content(
            left_sections, right_sections, matched_left, matched_right
        )

        candidate_edges: list[tuple[float, str, str]] = []
        for left_key, left_section in left_sections.items():
            if left_key in matched_left:
                continue
            left_chapter = self._chapter_key(left_key)
            for right_key, right_section in right_sections.items():
                if right_key in matched_right:
                    continue
                right_chapter = self._chapter_key(right_key)
                score = self._section_score(left_section, right_section)
                chapter_is_mapped = chapter_map.get(left_chapter) == right_chapter
                if chapter_is_mapped:
                    # Bonus for sections in a pre-matched chapter pair (lifts renumbered sections).
                    score = min(1.0, score + 0.20)
                else:
                    # Penalty for large chapter gap when chapters are NOT in the correspondence map:
                    # prevents marginal false matches across unrelated chapters.
                    score = max(0.0, score - self._chapter_divergence_penalty(left_key, right_key))
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
                # Prefer same-type matches (tables→tables, sections→sections)
                if left_node.get("node_type") == right_node.get("node_type"):
                    score = min(1.0, score + 0.05)
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

    def _resolve_split_table_pairs(
        self,
        *,
        left_nodes: list[dict],
        right_nodes: list[dict],
        matched_left: dict[str, ClauseMatch],
        matched_right_ids: set[str],
    ) -> None:
        """Match unmatched table nodes that are page-split fragments of a single logical table.

        Groups unmatched table nodes by (section_path, num_cols).  When a group has at
        least one unmatched node on each side, merges the fragments into a virtual node
        and scores the merged pair.  If the score meets the minimum clause threshold the
        fragments are registered as matched so they are not reported as REMOVED/ADDED.
        """
        def _table_group_key(node: dict) -> tuple[str, int] | None:
            if str(node.get("node_type") or "") != "table":
                return None
            section = str(node.get("section_path") or "")
            num_cols = int(node.get("table_num_cols") or 0)
            if num_cols == 0:
                return None
            return (section, num_cols)

        def _merge_virtual(nodes_group: list[dict]) -> dict:
            """Produce a single virtual node by merging all fragments."""
            if len(nodes_group) == 1:
                return nodes_group[0]
            combined_fps: list[str] = []
            combined_cells: list[dict] = []
            row_offset = 0
            total_rows = 0
            for seg in nodes_group:
                fps = list(seg.get("table_row_fingerprints") or [])
                combined_fps.extend(fps)
                rows = int(seg.get("table_num_rows") or 0)
                for cell in (seg.get("table_cells") or []):
                    combined_cells.append({**cell, "row": int(cell.get("row", 0)) + row_offset})
                row_offset += rows
                total_rows += rows
            base = dict(nodes_group[0])
            base["table_row_fingerprints"] = combined_fps
            base["table_cells"] = combined_cells
            base["table_num_rows"] = total_rows
            base["text"] = "\n".join(str(seg.get("text") or "") for seg in nodes_group)
            base["clean_text"] = "\n".join(str(seg.get("clean_text") or "") for seg in nodes_group)
            base["markdown_text"] = "\n".join(str(seg.get("markdown_text") or "") for seg in nodes_group)
            return base

        # Build groups of unmatched table nodes keyed by (section, num_cols)
        left_groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for node in left_nodes:
            lid = str(node.get("chunk_id") or "")
            if lid and lid not in matched_left:
                key = _table_group_key(node)
                if key:
                    left_groups[key].append(node)

        right_groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for node in right_nodes:
            rid = str(node.get("chunk_id") or "")
            if rid and rid not in matched_right_ids:
                key = _table_group_key(node)
                if key:
                    right_groups[key].append(node)

        for key in left_groups:
            if key not in right_groups:
                continue
            left_frags = left_groups[key]
            right_frags = right_groups[key]

            virtual_left = _merge_virtual(left_frags)
            virtual_right = _merge_virtual(right_frags)
            score = self._table_score(virtual_left, virtual_right)

            if score < self.thresholds.min_clause_score:
                continue

            distance = 1.0 - score
            # Register each left fragment matched to the first right fragment
            # (many-to-many virtual match — suppresses false REMOVED/ADDED)
            primary_right_id = str(right_frags[0].get("chunk_id") or "")
            for left_frag in left_frags:
                lid = str(left_frag.get("chunk_id") or "")
                if lid and lid not in matched_left:
                    matched_left[lid] = ClauseMatch(
                        distance=distance,
                        matched_by="split_table_merge",
                        left=left_frag,
                        right=right_frags[0],
                    )
            for right_frag in right_frags:
                rid = str(right_frag.get("chunk_id") or "")
                if rid:
                    matched_right_ids.add(rid)

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
                query_node_types = {"table"}
            else:
                query_node_types = search_node_types - {"table"} or search_node_types

            if self.search_fn is not None:
                candidates = self.search_fn(
                    query_string=str(left_node.get("section_path") or ""),
                    query_text=query_text,
                    target_document_id=target_document_id,
                    limit=self.topk,
                    node_types=sorted(query_node_types),
                )
                candidates = [
                    c for c in candidates
                    if str(c.get("section_path") or "") == right_section
                    and str(c.get("chunk_id") or "") not in matched_right_ids
                    and str(c.get("chunk_id") or "") in right_by_id
                ]
            else:
                # Local fallback: score all unmatched right nodes in the mapped section.
                candidates = [
                    node for node in right_by_id.values()
                    if str(node.get("section_path") or "") == right_section
                    and node.get("node_type") in query_node_types
                    and str(node.get("chunk_id") or "") not in matched_right_ids
                ]

            for candidate in candidates:
                right_id = str(candidate.get("chunk_id") or "")
                if not right_id or right_id in matched_right_ids or right_id not in right_by_id:
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
                matched_by="local_fallback" if self.search_fn is None else "vector_search",
                left=left_node,
                right=right_node,
            )
            matched_right_ids.add(right_id)

    # Matches leading section numbers like "3.", "3.1", "3.1.2", "A.1", "B.2.1" or
    # keyword prefixes like "Section 4", "Article 2.1", "Annex A.1", "Chapter 3 -"
    _SECTION_NUMBER_RE = re.compile(
        r'^(?:(?:section|article|chapter|clause|part|annex|appendix)\s+)?(?:[A-Z]\.)?[\d.]+\s*[-–:.]?\s*',
        re.IGNORECASE,
    )
    # Extracts the pure numeric portion of a section number (e.g. "3.2.1" from "3.2.1 Title")
    _SECTION_NUM_EXTRACT_RE = re.compile(r'^(?:[A-Z]\.)?(\d+(?:\.\d+)*)', re.IGNORECASE)
    # Detects a table/figure caption row: starts with "table N" or "figure N"
    _CAPTION_ROW_RE = re.compile(r'^(?:table|tbl\.?|figure|fig\.?)\s*\d', re.IGNORECASE)

    # Bilingual heading glossary: German canonical form → English canonical form
    # Applied token-by-token before SequenceMatcher to enable cross-language alignment
    HEADING_SYNONYMS: dict[str, str] = {
        "prüfung": "test",
        "prüfverfahren": "test method",
        "prüfschritt": "test step",
        "prüfaufbau": "test setup",
        "prüfbedingungen": "test conditions",
        "messung": "measurement",
        "messverfahren": "measurement method",
        "anforderung": "requirement",
        "anforderungen": "requirements",
        "anwendungsbereich": "scope",
        "geltungsbereich": "scope",
        "allgemein": "general",
        "allgemeines": "general",
        "begriffe": "terms",
        "definitionen": "definitions",
        "abkürzungen": "abbreviations",
        "symbole": "symbols",
        "normative verweise": "normative references",
        "literaturhinweise": "bibliography",
        "einleitung": "introduction",
        "grenzwert": "limit",
        "grenzwerte": "limits",
        "prüfpegel": "test level",
        "prüfpegel und klassen": "test levels and classes",
        "prüfaufbau und konfiguration": "test setup and configuration",
        "störfestigkeit": "immunity",
        "störaussendung": "emission",
        "frequenzbereich": "frequency range",
        "klimaprüfung": "climatic test",
        "schwingungsprüfung": "vibration test",
        "schockprüfung": "shock test",
        "sicherheit": "safety",
        "schutzgrad": "protection degree",
        # Additional German technical terms (post-accent-folding form)
        "systemtest": "system test",
        "systemprufung": "system test",       # after accent folding: Systemprüfung → systemprufung
        "systemprüfung": "system test",
        "ausfuehrung": "implementation",      # Ausführung
        "ausfuhrung": "implementation",
        "durchfuehrung": "procedure",         # Durchführung
        "durchfuhrung": "procedure",
        "pruefstand": "test bench",           # Prüfstand
        "prüfstand": "test bench",
        "auswertung": "evaluation",
        "beurteilung": "assessment",
        "ergebnis": "result",
        "ergebnisse": "results",
        "prüfergebnis": "test result",
        "pruefergebnis": "test result",
        "prüfbedingung": "test condition",
        "pruefbedingung": "test condition",
        "prüfkörper": "test specimen",
        "pruefkoerper": "test specimen",
        "grenzwert": "limit",
        "grenzwerte": "limits",
        "pegels": "level",
        "prüfpegel": "test level",
        "pruefpegel": "test level",
        "schutzziele": "protection objectives",
        "schutzmaßnahmen": "protective measures",
        "schutzmasnahmen": "protective measures",
    }

    # Pre-computed accent-folded version of HEADING_SYNONYMS for fast lookup.
    # Built lazily as a class attribute so it's shared across all instances.
    _FOLDED_SYNONYMS: dict[str, str] = {}

    @classmethod
    def _build_folded_synonyms(cls) -> None:
        """Populate _FOLDED_SYNONYMS from HEADING_SYNONYMS with folded keys."""
        fold_map = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                    "Ä": "ae", "Ö": "oe", "Ü": "ue"}

        def _fold(s: str) -> str:
            for src, dst in fold_map.items():
                s = s.replace(src, dst)
            return s.lower()

        cls._FOLDED_SYNONYMS = {_fold(k): v for k, v in cls.HEADING_SYNONYMS.items()}

    # Accent folding map: German umlauts → ASCII equivalents for comparison
    _ACCENT_FOLD: dict[str, str] = {
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
        "Ä": "ae", "Ö": "oe", "Ü": "ue",
    }

    def _fold_accents(self, text: str) -> str:
        for src, dst in self._ACCENT_FOLD.items():
            text = text.replace(src, dst)
        return text

    def _normalize_section_title(self, title: str) -> str:
        """Strip leading section numbers/keywords and apply bilingual synonym mapping.

        Improvements over baseline:
        - Whitespace normalization (collapse, strip)
        - Fused-word splitting (CamelCase and digit-letter boundaries)
        - German accent folding (ä→ae, ö→oe, ü→ue, ß→ss) for cross-version comparison
        - Extended bilingual HEADING_SYNONYMS including Systemtest/Systemprüfung etc.

        This enables SequenceMatcher to pair sections that were renamed
        from German to English (or vice versa), or where OCR fused words together.
        """
        # Step 0: Normalize whitespace; split fused words at CamelCase and digit-letter junctions
        t = re.sub(r"\s+", " ", title).strip()
        t = re.sub(r"([a-z])([A-Z])", r"\1 \2", t)      # "Testresult" → "Test result"
        t = re.sub(r"(\d)([A-Za-z])", r"\1 \2", t)      # "6.2.5Testresult" → "6.2.5 Testresult"
        t = re.sub(r"([A-Za-z])(\d)", r"\1 \2", t)      # "TestLevel3" → "Test Level 3"
        # Step 1: Accent folding before lowercasing
        t = self._fold_accents(t)
        t = t.lower()
        cleaned = self._SECTION_NUMBER_RE.sub('', t).strip()
        if not cleaned:
            return t
        # Step 2: Synonym substitution token by token (bigrams first, then unigrams)
        tokens = cleaned.split()
        normalized_tokens = []
        i = 0
        while i < len(tokens):
            if i + 1 < len(tokens):
                bigram = f"{tokens[i]} {tokens[i + 1]}"
                # Try both accent-folded form and original (dict may have either)
                bigram_folded = self._fold_accents(bigram)
                result = self.HEADING_SYNONYMS.get(bigram_folded) or self.HEADING_SYNONYMS.get(bigram)
                if result:
                    normalized_tokens.append(result)
                    i += 2
                    continue
            tok = tokens[i]
            tok_folded = self._fold_accents(tok)
            # Try: accent-folded, original, and with/without umlaut chars
            normalized_tokens.append(
                self.HEADING_SYNONYMS.get(tok_folded)
                or self.HEADING_SYNONYMS.get(tok)
                or self._FOLDED_SYNONYMS.get(tok_folded)
                or tok
            )
            i += 1
        return " ".join(normalized_tokens)

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

    def _numeric_overlap(self, left_text: str, right_text: str) -> float:
        """Jaccard overlap of numeric tokens between two texts."""
        left_nums = set(
            m.group(0).lower().replace(" ", "")
            for m in _NUMBER_EXTRACT_RE.finditer(left_text)
        )
        right_nums = set(
            m.group(0).lower().replace(" ", "")
            for m in _NUMBER_EXTRACT_RE.finditer(right_text)
        )
        if not left_nums and not right_nums:
            return 1.0
        if not left_nums or not right_nums:
            return 0.0
        return len(left_nums & right_nums) / len(left_nums | right_nums)

    def _entity_overlap(self, left_text: str, right_text: str) -> float:
        """Jaccard overlap of EMC domain entity tokens."""
        left_ents = set(m.group(0).lower() for m in _EMC_ENTITY_RE.finditer(left_text))
        right_ents = set(m.group(0).lower() for m in _EMC_ENTITY_RE.finditer(right_text))
        if not left_ents and not right_ents:
            return 1.0
        if not left_ents or not right_ents:
            return 0.0
        return len(left_ents & right_ents) / len(left_ents | right_ents)

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
        # When SequenceMatcher gives a low score, check token overlap (handles
        # cross-language synonyms not yet covered by HEADING_SYNONYMS).
        if title_score < 0.5:
            tok_overlap = token_overlap(left_title, right_title, self.language)
            title_score = max(title_score, tok_overlap * 0.8)
        content_score = token_overlap(left.clean_text, right.clean_text, self.language)
        numbering_score = self._numbering_depth_score(left.title, right.title)
        order_penalty = abs(left.order - right.order)
        path_score = 1.0 / (1 + order_penalty * 0.3)
        numeric_ov = self._numeric_overlap(left.clean_text, right.clean_text)
        return (
            0.20 * title_score
            + 0.15 * numbering_score
            + 0.10 * path_score
            + 0.45 * content_score
            + 0.10 * numeric_ov
        )

    @staticmethod
    def _chapter_divergence_penalty(left_key: str, right_key: str) -> float:
        """Return a score penalty (0.0 or 0.10) when top-level chapter numbers diverge significantly."""
        lm = re.match(r"^(\d+)", left_key.strip())
        rm = re.match(r"^(\d+)", right_key.strip())
        if lm and rm:
            diff = abs(int(lm.group(1)) - int(rm.group(1)))
            if diff > 3:
                return 0.10
        return 0.0

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

        numeric_ov = self._numeric_overlap(left_text, right_text)
        entity_ov = self._entity_overlap(left_text, right_text)
        score = (
            0.30 * text_score
            + 0.15 * lexical_score
            + 0.05 * length_score
            + 0.25 * meaning_score
            + 0.10 * signature_score
            + 0.10 * numeric_ov
            + 0.05 * entity_ov
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

    _LATEX_SUB_RE = re.compile(r'\$_\{([^}]+)\}\$|\$\^\{([^}]+)\}\$|\$([^$]+)\$')

    @staticmethod
    def _align_table_columns(
        left_headers: list[str], right_headers: list[str]
    ) -> dict[int, int]:
        """Map left column indices to right column indices by header similarity.

        Returns {left_col_idx: right_col_idx} for pairs whose SequenceMatcher
        ratio ≥ 0.60.  Stub columns (_row_label_N, column_N) are excluded from
        alignment so they don't distort the match.

        When most columns can be aligned (≥ 60% coverage), the remapping is used
        in cell scoring to handle column reordering between document versions.
        """
        from difflib import SequenceMatcher

        def _sig(h: str) -> str:
            return re.sub(r"\s+", " ", h.strip().lower())

        left_sigs = [_sig(h) for h in left_headers]
        right_sigs = [_sig(h) for h in right_headers]

        # Exclude placeholder columns from alignment
        def _is_stub(s: str) -> bool:
            return s.startswith("_row_label_") or s.startswith("column_")

        col_map: dict[int, int] = {}
        used_right: set[int] = set()

        edges: list[tuple[float, int, int]] = []
        for li, ls in enumerate(left_sigs):
            if _is_stub(ls):
                continue
            for ri, rs in enumerate(right_sigs):
                if _is_stub(rs) or ri in used_right:
                    continue
                score = SequenceMatcher(None, ls, rs).ratio()
                if score >= 0.60:
                    edges.append((score, li, ri))

        edges.sort(key=lambda e: e[0], reverse=True)
        for _, li, ri in edges:
            if li in col_map or ri in used_right:
                continue
            col_map[li] = ri
            used_right.add(ri)

        return col_map

    @staticmethod
    def _is_reliable_schema_signature(node: dict) -> bool:
        """Return False when the schema signature may be based on placeholder headers.

        Placeholder headers like "column_1" arise when row-0 cells are empty
        (stub/row-label columns).  Treating such signatures as unreliable prevents
        false schema mismatches across document versions of the same table.
        """
        schema = str(node.get("table_schema_signature") or "")
        if not schema:
            return False
        cells = node.get("table_cells") or []
        row0_cells = [c for c in cells if int(c.get("row", -1)) == 0]
        has_placeholder = any(
            str(c.get("text", "")).strip().lower().startswith("column_")
            for c in row0_cells
        )
        return not has_placeholder

    def _table_score(self, left: dict, right: dict) -> float:
        """Compute similarity score for tables with lenient handling of structural changes.

        Matching uses multiple strategies:
        1. Title/caption similarity (subject-level match)
        2. Cell content similarity (Jaccard set-based, ignoring row position shifts)
        3. Column structure preservation (schema signature match)
        4. Dimension similarity (handles added/removed rows gracefully)

        Table numbers can change (Tabelle 3 → Tabelle 5) without affecting match,
        as long as content is highly similar. This handles cases where:
        - Table captions are renumbered in a revision
        - Rows are added/removed but column structure is preserved
        - Cell content is modified but table structure remains

        Caption-row normalisation: some PDF extractors embed the table caption
        as a spanning row 0 rather than as an external title.  We detect and
        strip such rows before comparing cell grids and dimensions so that a
        table with an embedded caption is treated identically to one where the
        caption is stored externally.
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
        # Only use schema signature for scoring when headers are reliable (no placeholder columns)
        left_schema_reliable = self._is_reliable_schema_signature(left)
        right_schema_reliable = self._is_reliable_schema_signature(right)
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

        schema_score = 0.5  # neutral default when signatures are unreliable
        if left_schema and right_schema and left_schema_reliable and right_schema_reliable:
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
        # Strip trailing punctuation and normalize EMC/maritime unit formats so
        # "Level 3", "level  3", "LEVEL3" all compare equal; same for "1 V/m" vs "1V/m".
        _EMC_UNIT_RE = re.compile(
            r"(\d)\s*(v/m|a/m|db[µμu]v|db[µμu]a|mhz|ghz|khz|hz|ma|[µμu]a|ms|[µμu]s|kv/m)\b",
            re.IGNORECASE,
        )

        def _norm_cell(text: str) -> str:
            t = str(text).lower().strip()
            t = re.sub(r"\*{1,3}|_{1,3}", "", t)          # strip markdown bold/italic
            # Normalize LaTeX subscript/superscript: "u$_{n}$" → "un"
            t = self._LATEX_SUB_RE.sub(
                lambda m: (m.group(1) or m.group(2) or m.group(3) or ""), t
            )
            t = _EMC_UNIT_RE.sub(lambda m: m.group(1) + m.group(2).lower(), t)
            t = re.sub(r"\b(level|no\.?|class)\s+(\S)", lambda m: m.group(1) + " " + m.group(2), t)
            # Normalize multiplication operators (·, ×, x) for formula comparison
            t = t.replace("·", "*").replace("×", "*")
            t = re.sub(r"\s+", " ", t).strip()
            return t.rstrip(":.,;-")

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

        # Apply column alignment to handle column reordering between versions.
        # Extract column headers from row-0 cells of each table.
        def _extract_col_headers(cells_norm: list[dict]) -> list[str]:
            row0 = sorted(
                [c for c in cells_norm if int(c.get("row", -1)) == 0],
                key=lambda c: int(c.get("col", 0)),
            )
            return [str(c.get("text", "")).strip().lower() for c in row0]

        left_headers_raw = _extract_col_headers(left_cells_norm)
        right_headers_raw = _extract_col_headers(right_cells_norm)
        col_map = self._align_table_columns(left_headers_raw, right_headers_raw)

        # Remap right cell positions using column alignment when ≥50% of left
        # columns are matched (otherwise position-based comparison is more stable).
        alignment_coverage = len(col_map) / max(1, len(left_headers_raw))
        if col_map and alignment_coverage >= 0.5:
            right_cell_map_aligned = {
                (row, col_map.get(col, col)): text
                for (row, col), text in right_cell_map.items()
            }
        else:
            right_cell_map_aligned = right_cell_map

        all_positions = set(left_cell_map.keys()) | set(right_cell_map_aligned.keys())
        if not all_positions:
            cell_score = dim_score
        else:
            exact_matches = sum(
                1
                for pos in all_positions
                if left_cell_map.get(pos) == right_cell_map_aligned.get(pos)
                and left_cell_map.get(pos)  # Non-empty match
            )
            partial_matches = 0.0
            for pos in all_positions:
                left_val = left_cell_map.get(pos, "")
                right_val = right_cell_map_aligned.get(pos, "")
                if left_val and right_val and left_val != right_val:
                    overlap = token_overlap(left_val, right_val, self.language)
                    if overlap > 0.5:
                        partial_matches += overlap

            cell_score = (exact_matches + partial_matches) / len(all_positions)

        text_score = self._table_text_score(left, right)

        if has_title:
            if title_score >= 0.85:  # type: ignore[operator]
                base_with_title_match = (
                    0.40 * title_score  # type: ignore[operator]
                    + 0.30 * cell_score
                    + 0.10 * dim_score
                    + 0.08 * text_score
                    + 0.07 * schema_score
                    + 0.05 * row_fp_score
                )
                # For tables with identical caption AND column structure (e.g. DNV maritime
                # tables that appear in both versions), ensure we return at least 0.45 so the
                # match engine pairs them rather than treating one as ADDED and the other
                # REMOVED — which would inflate the reported impact to HIGH.
                if schema_score == 1.0:
                    base_with_title_match = max(base_with_title_match, 0.45)
                return base_with_title_match

            base_with_title_low = (
                0.20 * title_score  # type: ignore[operator]
                + 0.35 * cell_score
                + 0.15 * dim_score
                + 0.10 * text_score
                + 0.10 * schema_score
                + 0.10 * row_fp_score
            )

            # Fallback: If titles differ but cell content is very similar (>0.75),
            # consider it a table modification (name/number change, added rows, etc.)
            # rather than a different table. This handles cases like:
            # - "Tabelle 3: Risk Matrix" → "Tabelle 5: Risk Matrix" (renumbering)
            # - Same table with added/removed rows
            if cell_score > 0.75 and schema_score > 0.5:
                return max(base_with_title_low, 0.68)

            return base_with_title_low

        # No title available – fall back to structure-only weights.
        base_score = (
            0.20 * dim_score
            + 0.45 * cell_score
            + 0.15 * text_score
            + 0.10 * schema_score
            + 0.10 * row_fp_score
        )

        # Fallback: If cell content is very similar (Jaccard >0.75), match tables even if
        # dimension or name changed. This handles tables with added/removed rows that are
        # still fundamentally the same table.
        if base_score < 0.65 and cell_score > 0.75:
            # High cell content similarity overrides dimension/name differences
            return max(base_score, 0.65)

        return base_score

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
