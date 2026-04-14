from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from typing import Iterable

from grc_policy_server.models.schemas import KeyDifference
from grc_policy_server.utils.hashing import normalize_for_comparison

_TABLE_SEPARATOR_LINE_RE = re.compile(
    r"^\s*\|?\s*:?-{2,}:?(?:\s*\|\s*:?-{2,}:?)*\s*\|?\s*$",
    re.MULTILINE,
)
_RNG = random.SystemRandom()


def canonicalize_text_content(value: str) -> str:
    text = _TABLE_SEPARATOR_LINE_RE.sub("", value or "")
    return normalize_for_comparison(text)


def canonicalize_node_content(node: dict) -> str:
    node_type = str(node.get("node_type") or "clause")
    if node_type == "table":
        cells = node.get("table_cells") or []
        if cells:
            normalized_cells: list[str] = []
            for cell in cells:
                row = int(cell.get("row") or 0)
                col = int(cell.get("col") or 0)
                text = canonicalize_text_content(str(cell.get("text") or ""))
                if text:
                    normalized_cells.append(f"{row}:{col}:{text}")
            normalized_cells.sort()
            rows = int(node.get("table_num_rows") or 0)
            cols = int(node.get("table_num_cols") or 0)
            return f"rows={rows};cols={cols};" + "||".join(normalized_cells)

        markdown = str(
            node.get("markdown_text") or node.get("text") or node.get("clean_text") or ""
        )
        return canonicalize_text_content(markdown)

    return canonicalize_text_content(
        str(node.get("canonical_text") or node.get("clean_text") or node.get("text") or "")
    )


def build_section_canonical_map(nodes: Iterable[dict]) -> dict[str, Counter[str]]:
    by_section: dict[str, Counter[str]] = defaultdict(Counter)
    for node in nodes:
        if str(node.get("node_type") or "") not in {"clause", "table"}:
            continue
        section = str(node.get("section_path") or "Unknown Section")
        canonical = canonicalize_node_content(node)
        if canonical:
            by_section[section][canonical] += 1
    return dict(by_section)


def build_section_alignment_maps(
    section_matches: Iterable[tuple[str, str]],
) -> tuple[dict[str, str], dict[str, str]]:
    left_to_right: dict[str, str] = {}
    right_to_left: dict[str, str] = {}
    for left, right in section_matches:
        left_key = str(left or "").strip()
        right_key = str(right or "").strip()
        if not left_key or not right_key:
            continue
        left_to_right.setdefault(left_key, right_key)
        right_to_left.setdefault(right_key, left_key)
    return left_to_right, right_to_left


def find_unchanged_section_pairs(
    *,
    section_matches: Iterable[tuple[str, str]],
    left_nodes: Iterable[dict],
    right_nodes: Iterable[dict],
) -> set[tuple[str, str]]:
    left_map = build_section_canonical_map(left_nodes)
    right_map = build_section_canonical_map(right_nodes)
    unchanged: set[tuple[str, str]] = set()
    for left, right in section_matches:
        left_key = str(left or "").strip()
        right_key = str(right or "").strip()
        if not left_key or not right_key:
            continue
        if left_map.get(left_key, Counter()) == right_map.get(right_key, Counter()):
            unchanged.add((left_key, right_key))
    return unchanged


def filter_key_differences(
    diffs: list[KeyDifference],
    *,
    unchanged_section_pairs: set[tuple[str, str]],
    left_to_right: dict[str, str],
    right_to_left: dict[str, str],
) -> list[KeyDifference]:
    filtered: list[KeyDifference] = []
    for diff in diffs:
        pair = _resolve_diff_section_pair(
            diff,
            left_to_right=left_to_right,
            right_to_left=right_to_left,
        )
        if pair and pair in unchanged_section_pairs:
            continue

        if diff.changeType == "MODIFIED":
            old_text = _diff_text(diff.doc1Reference.sourceText if diff.doc1Reference else None, diff.doc1Content)
            new_text = _diff_text(diff.doc2Reference.sourceText if diff.doc2Reference else None, diff.doc2Content)
            if old_text and old_text == new_text:
                continue

        filtered.append(diff)
    return filtered


def random_diff_subset(diffs: list[KeyDifference], max_items: int = 10) -> list[KeyDifference]:
    if max_items <= 0:
        return []
    if len(diffs) <= max_items:
        return list(diffs)
    return _RNG.sample(list(diffs), k=max_items)


def _diff_text(source_text: str | None, fallback: str | None) -> str:
    return canonicalize_text_content(str(source_text or fallback or ""))


def _resolve_diff_section_pair(
    diff: KeyDifference,
    *,
    left_to_right: dict[str, str],
    right_to_left: dict[str, str],
) -> tuple[str, str] | None:
    left_section = str(diff.doc1Reference.section) if diff.doc1Reference else ""
    right_section = str(diff.doc2Reference.section) if diff.doc2Reference else ""

    if left_section and right_section:
        return left_section, right_section
    if left_section and left_section in left_to_right:
        return left_section, left_to_right[left_section]
    if right_section and right_section in right_to_left:
        return right_to_left[right_section], right_section
    return None
