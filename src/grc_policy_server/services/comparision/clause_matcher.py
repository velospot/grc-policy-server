from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable

from grc_policy_server.utils.hashing import normalize_text


@dataclass(frozen=True)
class MatchThresholds:
    max_match_distance: float = 0.50
    unchanged_distance: float = 0.15
    modified_distance: float = 0.25


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
        left = self._select_clause_nodes(left_nodes)
        right = self._select_clause_nodes(right_nodes)
        right_by_id = {node.get("chunk_id") or "": node for node in right}
        matched_left: dict[str, ClauseMatch] = {}
        matched_right_ids: set[str] = set()

        left_by_stable = Counter(node.get("stable_id") or "" for node in left if node.get("stable_id"))
        right_by_stable = defaultdict(list)
        for node in right:
            stable_id = node.get("stable_id")
            if stable_id:
                right_by_stable[stable_id].append(node)

        for left_node in left:
            left_id = left_node.get("chunk_id") or ""
            stable_id = left_node.get("stable_id") or ""
            if not left_id or not stable_id:
                continue
            if left_by_stable[stable_id] != 1 or len(right_by_stable.get(stable_id, [])) != 1:
                continue
            right_node = right_by_stable[stable_id][0]
            right_id = right_node.get("chunk_id") or ""
            if right_id in matched_right_ids:
                continue
            matched_left[left_id] = ClauseMatch(
                distance=self._text_distance(left_node, right_node),
                matched_by="stable_id",
                left=left_node,
                right=right_node,
            )
            matched_right_ids.add(right_id)

        candidate_edges: list[tuple[float, str, str, dict, dict]] = []
        node_types = sorted({str(node.get("node_type") or "clause") for node in right})
        for left_node in left:
            left_id = left_node.get("chunk_id") or ""
            if not left_id or left_id in matched_left:
                continue
            text = (left_node.get("text") or "").strip()
            if not text:
                continue
            matches = self.search_fn(
                query_string=str(left_node.get("section_path") or ""),
                query_text=text,
                target_document_id=target_document_id,
                limit=self.topk,
                node_types=node_types,
            )
            for candidate in matches:
                right_id = candidate.get("chunk_id") or ""
                if not right_id or right_id in matched_right_ids:
                    continue
                if right_id not in right_by_id:
                    continue
                distance = candidate.get("_distance")
                if not isinstance(distance, (int, float)):
                    distance = self._text_distance(left_node, candidate)
                distance = float(distance)
                if distance > self.thresholds.max_match_distance:
                    continue
                candidate_edges.append(
                    (distance, left_id, right_id, left_node, right_by_id[right_id])
                )

        candidate_edges.sort(key=lambda item: item[0])
        for distance, left_id, right_id, left_node, right_node in candidate_edges:
            if left_id in matched_left or right_id in matched_right_ids:
                continue
            matched_left[left_id] = ClauseMatch(
                distance=distance,
                matched_by="vector_search",
                left=left_node,
                right=right_node,
            )
            matched_right_ids.add(right_id)

        removed = [
            node
            for node in left
            if (node.get("chunk_id") or "") not in matched_left
        ]
        added = [
            node
            for node in right
            if (node.get("chunk_id") or "") not in matched_right_ids
        ]
        return ClauseMatchingResult(
            matches=list(matched_left.values()),
            removed=removed,
            added=added,
        )

    def _select_clause_nodes(self, nodes: list[dict]) -> list[dict]:
        clauses = [node for node in nodes if node.get("node_type") == "clause"]
        if clauses:
            return clauses
        clauses_and_tables = [
            node for node in nodes if node.get("node_type") in {"clause", "table"}
        ]
        if clauses_and_tables:
            return clauses_and_tables
        return nodes

    def _text_distance(self, left: dict, right: dict) -> float:
        left_text = normalize_text(str(left.get("text") or ""))
        right_text = normalize_text(str(right.get("text") or ""))
        if not left_text and not right_text:
            return 0.0
        if not left_text or not right_text:
            return 1.0
        return 1.0 - SequenceMatcher(None, left_text, right_text).ratio()
