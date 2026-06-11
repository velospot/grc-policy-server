from __future__ import annotations

import re
from dataclasses import dataclass

from grc_policy_server.services.graph.docling_graph_adapter import (
    DoclingGraphArtifact,
    DoclingGraphNode,
)

_WORD_RE = re.compile(r"\b\w+\b")
_SPLIT_CONFIDENCE_THRESHOLD = 0.70
_TOKEN_OVERLAP_MIN = 0.75
_PREFIX_WORDS_MIN = 2


@dataclass(frozen=True)
class SplitGroup:
    source_node_id: str
    target_node_ids: list[str]
    alignment_type: str  # "SPLIT" | "MERGE"
    confidence: float
    review_required: bool


@dataclass(frozen=True)
class SplitMergeResult:
    split_groups: list[SplitGroup]


class SplitMergeDetector:
    """Deterministic split/merge detection using heading-prefix and token-overlap signals.

    A section is classified SPLIT when one left node's title is a prefix of multiple
    right node titles AND the aggregated right text covers ≥75% of the left text tokens.
    MERGE is the mirror image (multiple left → one right).

    Only deterministic signals are used here. Confidence < threshold sets
    review_required=True as a hook for Agent A4 (not yet wired).
    """

    def detect(
        self,
        unmatched_left: list[DoclingGraphNode],
        unmatched_right: list[DoclingGraphNode],
        artifact_left: DoclingGraphArtifact,  # noqa: ARG002
        artifact_right: DoclingGraphArtifact,  # noqa: ARG002
    ) -> SplitMergeResult:
        groups: list[SplitGroup] = []
        groups.extend(self._detect_splits(unmatched_left, unmatched_right))
        groups.extend(self._detect_merges(unmatched_left, unmatched_right))
        return SplitMergeResult(split_groups=groups)

    def _detect_splits(
        self,
        left_nodes: list[DoclingGraphNode],
        right_nodes: list[DoclingGraphNode],
    ) -> list[SplitGroup]:
        groups: list[SplitGroup] = []
        used_right: set[str] = set()

        for left in left_nodes:
            left_title_words = _title_words(left.title)
            if len(left_title_words) < _PREFIX_WORDS_MIN:
                continue

            matching_right = [
                r for r in right_nodes
                if r.node_id not in used_right and _is_prefix_of(left_title_words, _title_words(r.title))
            ]
            if len(matching_right) < 2:
                continue

            # Signal 2: token overlap
            left_tokens = _text_tokens(left.text)
            right_tokens: set[str] = set()
            for r in matching_right:
                right_tokens.update(_text_tokens(r.text))
            if not left_tokens:
                continue
            overlap = len(left_tokens & right_tokens) / len(left_tokens)
            if overlap < _TOKEN_OVERLAP_MIN:
                continue

            confidence = round(overlap, 3)
            used_right.update(r.node_id for r in matching_right)
            groups.append(
                SplitGroup(
                    source_node_id=left.node_id,
                    target_node_ids=[r.node_id for r in matching_right],
                    alignment_type="SPLIT",
                    confidence=confidence,
                    review_required=confidence < _SPLIT_CONFIDENCE_THRESHOLD,
                )
            )

        return groups

    def _detect_merges(
        self,
        left_nodes: list[DoclingGraphNode],
        right_nodes: list[DoclingGraphNode],
    ) -> list[SplitGroup]:
        # Mirror of _detect_splits: multiple left → one right
        groups: list[SplitGroup] = []
        used_left: set[str] = set()

        for right in right_nodes:
            right_title_words = _title_words(right.title)
            if len(right_title_words) < _PREFIX_WORDS_MIN:
                continue

            matching_left = [
                l for l in left_nodes
                if l.node_id not in used_left and _is_prefix_of(right_title_words, _title_words(l.title))
            ]
            if len(matching_left) < 2:
                continue

            right_tokens = _text_tokens(right.text)
            left_tokens: set[str] = set()
            for l in matching_left:
                left_tokens.update(_text_tokens(l.text))
            if not right_tokens:
                continue
            overlap = len(right_tokens & left_tokens) / len(right_tokens)
            if overlap < _TOKEN_OVERLAP_MIN:
                continue

            confidence = round(overlap, 3)
            used_left.update(l.node_id for l in matching_left)
            groups.append(
                SplitGroup(
                    source_node_id=right.node_id,
                    target_node_ids=[l.node_id for l in matching_left],
                    alignment_type="MERGE",
                    confidence=confidence,
                    review_required=confidence < _SPLIT_CONFIDENCE_THRESHOLD,
                )
            )

        return groups


def _title_words(title: str | None) -> list[str]:
    return _WORD_RE.findall((title or "").lower())


def _text_tokens(text: str | None) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def _is_prefix_of(prefix_words: list[str], candidate_words: list[str]) -> bool:
    """Return True if prefix_words is a word-level prefix of candidate_words."""
    if not prefix_words or len(candidate_words) < len(prefix_words):
        return False
    return candidate_words[: len(prefix_words)] == prefix_words
