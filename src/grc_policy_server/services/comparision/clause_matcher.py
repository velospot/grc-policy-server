from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable

from grc_policy_server.services.comparision.policy_semantics import (
    ClauseMeaning,
    clean_policy_text,
    compare_clause_meaning,
    extract_clause_meaning,
    semantic_signature,
    token_overlap,
)


@dataclass(frozen=True)
class MatchThresholds:
    max_match_distance: float = 0.50
    unchanged_distance: float = 0.15
    modified_distance: float = 0.25
    min_section_score: float = 0.55
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
    ) -> None:
        self.search_fn = search_fn
        self.thresholds = thresholds
        self.topk = topk

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
        )

    def _select_content_nodes(self, nodes: list[dict]) -> list[dict]:
        content_nodes = [
            node for node in nodes if node.get("node_type") in {"clause", "table"}
        ]
        if content_nodes:
            return sorted(content_nodes, key=self._node_sort_key)
        clause_nodes = [node for node in nodes if node.get("node_type") == "clause"]
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
            grouped_items[str(node.get("section_path") or "Unknown Section")].append(node)

        buckets: dict[str, _SectionBucket] = {}
        for order, section_path in enumerate(
            sorted(grouped_items, key=lambda path: self._section_sort_key(grouped_items[path]))
        ):
            items = sorted(grouped_items[section_path], key=self._node_sort_key)
            section_node = section_nodes.get(section_path)
            stable_id = str(
                (section_node or {}).get("stable_id")
                or (section_node or {}).get("section_path")
                or section_path
            )
            title = str((section_node or {}).get("title") or section_path.split(" / ")[-1])
            clean_text = str((section_node or {}).get("clean_text") or "").strip()
            if not clean_text:
                clean_text = " ".join(self._node_clean_text(item) for item in items if item)
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

        left_by_stable = Counter(section.stable_id for section in left_sections.values())
        right_by_stable = Counter(section.stable_id for section in right_sections.values())
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
            str(node.get("stable_id") or "") for node in left_items if node.get("stable_id")
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
            if left_by_stable[stable_id] != 1 or len(right_by_stable.get(stable_id, [])) != 1:
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
                    candidate_edges.append((score, left_id, right_id, left_node, right_node))

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

    def _vector_fallback(
        self,
        *,
        left_nodes: list[dict],
        right_by_id: dict[str, dict],
        matched_left: dict[str, ClauseMatch],
        matched_right_ids: set[str],
        target_document_id: str,
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

        candidate_edges: list[tuple[float, str, str, dict, dict]] = []
        for left_node in left_nodes:
            left_id = str(left_node.get("chunk_id") or "")
            if not left_id or left_id in matched_left:
                continue
            query_text = self._node_clean_text(left_node)
            if not query_text:
                continue
            matches = self.search_fn(
                query_string=str(left_node.get("section_path") or ""),
                query_text=query_text,
                target_document_id=target_document_id,
                limit=self.topk,
                node_types=node_types,
            )
            for candidate in matches:
                right_id = str(candidate.get("chunk_id") or "")
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
                candidate_edges.append((score, left_id, right_id, left_node, right_node))

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

    def _section_score(self, left: _SectionBucket, right: _SectionBucket) -> float:
        title_score = SequenceMatcher(
            None,
            left.title.lower(),
            right.title.lower(),
        ).ratio()
        text_score = token_overlap(left.clean_text, right.clean_text)
        order_penalty = abs(left.order - right.order)
        order_score = 1.0 / (1 + order_penalty)
        return 0.45 * title_score + 0.40 * text_score + 0.15 * order_score

    def _clause_score(self, left: dict, right: dict) -> float:
        left_text = self._node_clean_text(left)
        right_text = self._node_clean_text(right)
        if not left_text and not right_text:
            return 1.0
        if not left_text or not right_text:
            return 0.0

        text_score = SequenceMatcher(None, left_text, right_text).ratio()
        lexical_score = token_overlap(left_text, right_text)
        length_score = self._length_similarity(left_text, right_text)
        meaning_score = self._meaning_score(left, right)
        signature_score = token_overlap(
            self._semantic_signature_from_node(left),
            self._semantic_signature_from_node(right),
        )

        score = (
            0.35 * text_score
            + 0.10 * lexical_score
            + 0.05 * length_score
            + 0.35 * meaning_score
            + 0.15 * signature_score
        )
        if left.get("node_type") != right.get("node_type"):
            score *= 0.8
        return max(0.0, min(score, 1.0))

    def _meaning_score(self, left: dict, right: dict) -> float:
        comparison = compare_clause_meaning(
            self._node_meaning(left),
            self._node_meaning(right),
        )
        score = comparison.score
        if comparison.obligation_change in {"strengthened", "weakened"} and score >= 0.35:
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
            node.get(field)
            for field in ("subject", "action", "object", "condition")
        ):
            return " | ".join(
                str(node.get(field) or "")
                for field in ("subject", "action", "object", "condition")
                if node.get(field)
            )
        return semantic_signature(str(node.get("text") or ""))

    def _node_clean_text(self, node: dict) -> str:
        return str(node.get("clean_text") or clean_policy_text(str(node.get("text") or ""))).strip()

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
