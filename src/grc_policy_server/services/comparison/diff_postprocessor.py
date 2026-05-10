from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Iterable

from grc_policy_server.models.schemas import KeyDifference
from grc_policy_server.utils.hashing import normalize_for_comparison

_TABLE_SEPARATOR_LINE_RE = re.compile(
    r"^\s*\|?\s*:?-{2,}:?(?:\s*\|\s*:?-{2,}:?)*\s*\|?\s*$",
    re.MULTILINE,
)
_TABLE_CAPTION_NUM_RE = re.compile(r"\bTabell?e\s+(\d+[A-Za-z]?)\b", re.IGNORECASE)
# Sections that contain reference/legend material, not normative policy requirements.
# Diffs from these sections are excluded from key differences entirely.
_REFERENCE_SECTION_RE = re.compile(
    r"\b(legende|symbole?|abkürzung(?:en)?|definitionen?|begriffe?|inhalt"
    r"|glossar|annex|anhang|abbreviation|legend|symbol|glossary|definition)\b",
    re.IGNORECASE,
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
        # Drop diffs from reference/legend sections — these are not normative content.
        section = str(diff.section or "")
        if not section and diff.doc1Reference:
            section = str(diff.doc1Reference.section or "")
        if not section and diff.doc2Reference:
            section = str(diff.doc2Reference.section or "")
        if _REFERENCE_SECTION_RE.search(section) and not _TABLE_CAPTION_NUM_RE.search(section):
            continue

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

    return _consolidate_split_table_diffs(filtered)


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


# ---------------------------------------------------------------------------
# Page-split table consolidation
# ---------------------------------------------------------------------------

_SUPPRESS_OVERLAP = 0.70   # identical content — suppress both REMOVED + ADDED
_MERGE_OVERLAP = 0.30      # partial overlap — collapse to a single MODIFIED


def _consolidate_split_table_diffs(diffs: list[KeyDifference]) -> list[KeyDifference]:
    """Collapse REMOVED + ADDED table pairs caused by page-boundary splits.

    Two passes:
    1. Exact section-name match — groups REMOVED/ADDED in the same section.
    2. Caption-number fallback — matches unresolved groups whose section names share
       the same "Tabelle N" number (handles minor section-name drift between doc versions).

    Overlap thresholds:
      ≥ 0.70 → suppress both (same content, different pagination)
      0.30–0.70 → collapse to a single MODIFIED diff
      < 0.30 → genuine change, keep all diffs
    """
    table_removed: dict[str, list[KeyDifference]] = defaultdict(list)
    table_added: dict[str, list[KeyDifference]] = defaultdict(list)
    other: list[KeyDifference] = []

    for diff in diffs:
        node_type = _node_type_from_diff(diff)
        if node_type != "table":
            other.append(diff)
            continue
        section = _diff_section(diff)
        if diff.changeType == "REMOVED":
            table_removed[section].append(diff)
        elif diff.changeType == "ADDED":
            table_added[section].append(diff)
        else:
            other.append(diff)

    result = list(other)
    handled_removed: set[str] = set()
    handled_added: set[str] = set()

    # Pass 1 — exact section match
    for section, removed_group in table_removed.items():
        added_group = table_added.get(section)
        if not added_group:
            continue
        _apply_overlap_decision(result, section, removed_group, added_group)
        handled_removed.add(section)
        handled_added.add(section)

    # Pass 2 — caption-number fallback for unmatched groups
    unmatched_removed = {s: g for s, g in table_removed.items() if s not in handled_removed}
    unmatched_added = {s: g for s, g in table_added.items() if s not in handled_added}

    # Build caption-number → section maps for unmatched groups
    cap_to_removed: dict[str, str] = {}
    for section in unmatched_removed:
        cap = _caption_number_from_section(section)
        if cap:
            cap_to_removed.setdefault(cap, section)

    cap_to_added: dict[str, str] = {}
    for section in unmatched_added:
        cap = _caption_number_from_section(section)
        if cap:
            cap_to_added.setdefault(cap, section)

    for cap, removed_section in cap_to_removed.items():
        added_section = cap_to_added.get(cap)
        if not added_section:
            continue
        _apply_overlap_decision(
            result,
            removed_section,
            unmatched_removed[removed_section],
            unmatched_added[added_section],
        )
        handled_removed.add(removed_section)
        handled_added.add(added_section)

    # Keep anything not handled
    for section, group in table_removed.items():
        if section not in handled_removed:
            result.extend(group)
    for section, group in table_added.items():
        if section not in handled_added:
            result.extend(group)

    return result


def _apply_overlap_decision(
    result: list[KeyDifference],
    section: str,
    removed_group: list[KeyDifference],
    added_group: list[KeyDifference],
) -> None:
    left_text = _join_source_texts(removed_group)
    right_text = _join_source_texts(added_group)
    overlap = _text_overlap(left_text, right_text)

    if overlap >= _SUPPRESS_OVERLAP:
        pass  # same content, different pagination — drop both
    elif overlap >= _MERGE_OVERLAP:
        anchor_removed = removed_group[0]
        anchor_added = added_group[0]
        result.append(
            KeyDifference(
                changeType="MODIFIED",
                section=section,
                doc1Content=anchor_removed.doc1Content,
                doc2Content=anchor_added.doc2Content,
                impact=anchor_removed.impact,
                doc1Reference=anchor_removed.doc1Reference,
                doc2Reference=anchor_added.doc2Reference,
            )
        )
    else:
        result.extend(removed_group)
        result.extend(added_group)


def _caption_number_from_section(section: str) -> str | None:
    m = _TABLE_CAPTION_NUM_RE.search(section)
    return m.group(1).lower() if m else None


def _node_type_from_diff(diff: KeyDifference) -> str:
    # KeyDifference doesn't carry node_type directly; we infer from content shape.
    # Table diffs typically have doc1Content / doc2Content starting with "Table (" or "|"
    for content in (diff.doc1Content, diff.doc2Content):
        if content:
            stripped = content.strip()
            if stripped.startswith("Table (") or stripped.startswith("|"):
                return "table"
    # Fall back to checking if the source texts look like markdown tables
    for ref in (diff.doc1Reference, diff.doc2Reference):
        if ref and ref.sourceText and ref.sourceText.strip().startswith("|"):
            return "table"
    return "other"


def _diff_section(diff: KeyDifference) -> str:
    if diff.doc1Reference:
        return str(diff.doc1Reference.section)
    if diff.doc2Reference:
        return str(diff.doc2Reference.section)
    return ""


def _join_source_texts(diffs: list[KeyDifference]) -> str:
    parts: list[str] = []
    for diff in diffs:
        for ref in (diff.doc1Reference, diff.doc2Reference):
            if ref and ref.sourceText:
                parts.append(canonicalize_text_content(ref.sourceText))
    return " ".join(parts)


def _text_overlap(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()
