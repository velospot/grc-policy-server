from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from grc_policy_server.models.schemas import ChangeDetail, DocumentReference
from grc_policy_server.services.comparison.severity_classifier import (
    ClassificationContext,
    SeverityClassifier,
)

ChangeType = Literal["ADDED", "REMOVED", "MODIFIED"]

_NUMBER_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:%|percent|days?|weeks?|months?|years?|"
    r"hours?|minutes?|seconds?|kg|g|mg|ms|s|m|cm|mm|w|kw|v|a)?\b",
    re.IGNORECASE,
)
# Cross-reference patterns: "Figure 3", "Table 5.2", "Section 3.1.2", "Annex A.1", etc.
# Changes to these are structural reordering artefacts, not semantic content changes.
_REF_NUM_RE = re.compile(
    r"\b(?:figure|fig\.?|table|tbl\.?|section|sec\.?|clause|annex|"
    r"appendix|chapter|part|article)\s*(?:[A-Z]\.)?[\d]+(?:[.\-][\d]+)*",
    re.IGNORECASE,
)
# Characters that are purely formatting — changes to these alone carry no semantic weight.
_FORMATTING_STRIP_RE = re.compile(r"[-–—\n\r;]")
_REQ_VERB_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bshall\s+not\b", "shall_not"),
    (r"\bmust\s+not\b", "must_not"),
    (r"\b(?:prohibited|forbidden|not\s+permitted)\b", "must_not"),
    (r"\bshall\b", "shall"),
    (r"\bmust\b", "must"),
    (r"\b(?:required|mandatory|obligatoire|requis|erforderlich)\b", "required"),
    (r"\b(?:should|soll(?:en)?|devrait)\b", "should"),
    (r"\b(?:recommended|recommendation|empfohlen)\b", "recommended"),
    (r"\b(?:may|optional|peut|kann)\b", "may"),
)
_REQ_STRENGTH = {
    "": 0,
    "may": 1,
    "recommended": 2,
    "should": 3,
    "required": 4,
    "must": 5,
    "shall": 6,
    "must_not": 7,
    "shall_not": 8,
}


@dataclass(frozen=True)
class ChangeRecord:
    change_id: str
    change_type: ChangeType
    alignment_type: str
    left_nodes: list[dict[str, Any]]
    right_nodes: list[dict[str, Any]]
    distance: float | None
    confidence: float
    node_type: str
    section: str
    doc1_content: str | None
    doc2_content: str | None
    doc1_reference: DocumentReference | None
    doc2_reference: DocumentReference | None
    changes: list[ChangeDetail]
    meaning_change: str = "unchanged"
    numeric_changes: list[dict[str, Any]] = field(default_factory=list)
    requirement_verb_change: dict[str, str] | None = None
    table_changes: list[dict[str, Any]] = field(default_factory=list)
    significance: str = "medium"
    impact: str = "Medium"
    severity: Literal["low", "medium", "high"] = "medium"
    requires_human_review: bool = False
    significance_reasons: list[str] = field(default_factory=list)
    # Evidence pack: source location for each version (doc1 = v1, doc2 = v2)
    v1_evidence: list[dict[str, Any]] = field(default_factory=list)
    v2_evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_trace_payload(self) -> dict[str, Any]:
        return {
            "changeId": self.change_id,
            "changeType": self.change_type,
            "alignmentType": self.alignment_type,
            "leftNodeIds": [str(node.get("node_id") or node.get("chunk_id") or "") for node in self.left_nodes],
            "rightNodeIds": [str(node.get("node_id") or node.get("chunk_id") or "") for node in self.right_nodes],
            "leftHeadingPath": _heading_path(self.left_nodes),
            "rightHeadingPath": _heading_path(self.right_nodes),
            "headingContext": _heading_context(self.left_nodes, self.right_nodes),
            "distance": self.distance,
            "confidence": self.confidence,
            "nodeType": self.node_type,
            "section": self.section,
            "impact": self.impact,
            "changeSeverity": self.severity,
            "significance": self.significance,
            "significanceReasons": self.significance_reasons,
            "numericChanges": self.numeric_changes,
            "requirementVerbChange": self.requirement_verb_change,
            "tableChanges": self.table_changes,
            "changes": [change.model_dump(mode="json") for change in self.changes],
            "doc1Reference": (
                self.doc1_reference.model_dump(mode="json")
                if self.doc1_reference
                else None
            ),
            "doc2Reference": (
                self.doc2_reference.model_dump(mode="json")
                if self.doc2_reference
                else None
            ),
            "v1Evidence": self.v1_evidence,
            "v2Evidence": self.v2_evidence,
        }

    def to_llm_payload(self) -> dict[str, Any]:
        payload = self.to_trace_payload()
        payload["doc1Content"] = self.doc1_content
        payload["doc2Content"] = self.doc2_content
        return payload


def detect_requirement_verb_change(
    left_text: str | None,
    right_text: str | None,
) -> dict[str, str] | None:
    left = detect_requirement_verb(left_text or "")
    right = detect_requirement_verb(right_text or "")
    if left == right:
        return None
    if _REQ_STRENGTH.get(right, 0) > _REQ_STRENGTH.get(left, 0):
        direction = "strengthened"
    elif _REQ_STRENGTH.get(right, 0) < _REQ_STRENGTH.get(left, 0):
        direction = "weakened"
    else:
        direction = "changed"
    return {"old": left, "new": right, "direction": direction}


def detect_requirement_verb(text: str) -> str:
    lowered = (text or "").lower()
    for pattern, value in _REQ_VERB_PATTERNS:
        if re.search(pattern, lowered):
            return value
    return ""


def detect_numeric_changes(
    left_text: str | None,
    right_text: str | None,
) -> list[dict[str, Any]]:
    left_values = _extract_numbers(left_text or "")
    right_values = _extract_numbers(right_text or "")
    left_set = set(left_values)
    right_set = set(right_values)
    if left_set == right_set:
        return []

    changes: list[dict[str, Any]] = []
    removed = sorted(left_set - right_set)
    added = sorted(right_set - left_set)
    for old, new in zip(removed, added, strict=False):
        changes.append({"type": "modified", "old": old, "new": new})
    for old in removed[len(added) :]:
        changes.append({"type": "removed", "old": old, "new": None})
    for new in added[len(removed) :]:
        changes.append({"type": "added", "old": None, "new": new})
    return changes


def is_formatting_only_change(
    left_text: str | None,
    right_text: str | None,
) -> bool:
    """Return True when the only differences are formatting characters:
    newlines, hyphens/dashes, semicolons, and extra whitespace.

    These carry no semantic weight and should produce LOW severity.
    """
    left = (left_text or "").strip()
    right = (right_text or "").strip()
    if not left or not right or left == right:
        return False

    def _strip(t: str) -> str:
        t = _FORMATTING_STRIP_RE.sub(" ", t)
        return re.sub(r"\s+", " ", t).strip().lower()

    return _strip(left) == _strip(right)


def is_reference_number_only_change(
    left_text: str | None,
    right_text: str | None,
) -> bool:
    """Return True when the only numeric differences between two texts are changes
    to figure/table/section/annex cross-reference numbers (e.g. "Figure 3" → "Figure 5",
    "Section 3.1" → "Section 4.2").  These are structural reordering artefacts and
    carry no semantic weight, so callers should down-classify them to LOW severity.

    The test: strip all reference-number patterns from both texts and recheck whether
    any numeric differences remain.  If none remain, every numeric change was a
    reference number.
    """
    left = left_text or ""
    right = right_text or ""

    # Fast path: no numeric difference at all.
    if set(_extract_numbers(left)) == set(_extract_numbers(right)):
        return False

    left_stripped = _REF_NUM_RE.sub("", left)
    right_stripped = _REF_NUM_RE.sub("", right)
    return set(_extract_numbers(left_stripped)) == set(_extract_numbers(right_stripped))


_CLASSIFIER = SeverityClassifier()


def classify_significance(
    *,
    change_type: ChangeType,
    alignment_type: str,
    node_type: str,
    distance: float | None,
    meaning_change: str,
    numeric_changes: list[dict[str, Any]],
    requirement_verb_change: dict[str, str] | None,
    table_changes: list[dict[str, Any]],
    cosmetic_change: bool = False,
    reference_number_only_change: bool = False,
    formatting_only_change: bool = False,
) -> tuple[str, str, Literal["low", "medium", "high"], list[str]]:
    """Delegate to SeverityClassifier and return the legacy (significance, impact, severity, reasons) tuple."""
    ctx = ClassificationContext(
        change_type=change_type,  # type: ignore[arg-type]
        alignment_type=alignment_type,
        node_type=node_type,
        distance=distance,
        meaning_change=meaning_change,
        numeric_changes=numeric_changes,
        requirement_verb_change=requirement_verb_change,
        table_changes=table_changes,
        cosmetic_change=cosmetic_change,
        reference_number_only_change=reference_number_only_change,
        formatting_only_change=formatting_only_change,
    )
    result = _CLASSIFIER.classify(ctx)
    return result.severity, result.impact, result.severity, result.reasons


def _extract_numbers(text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", match.group(0).strip().lower())
        for match in _NUMBER_RE.finditer(text or "")
    ]


def is_cosmetic_text_change(left_text: str | None, right_text: str | None) -> bool:
    left = (left_text or "").strip()
    right = (right_text or "").strip()
    if not left or not right or left == right:
        return False
    return _normalize_cosmetic_text(left) == _normalize_cosmetic_text(right) or (
        _compact_alnum(left) == _compact_alnum(right)
    )


_CAMEL_SPLIT_RE = re.compile(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')


def _normalize_cosmetic_text(text: str) -> str:
    text = _CAMEL_SPLIT_RE.sub(' ', text)
    translated = text.casefold().translate(
        {
            ord("“"): '"',
            ord("”"): '"',
            ord("‘"): ''',
            ord("’"): ''',
            ord("–"): '-',
            ord("—"): '-',
            ord("−"): '-',
            ord("…"): '...',
        }
    )
    chars = [char if char.isalnum() else ' ' for char in translated]
    return re.sub(r'\s+', ' ', ''.join(chars)).strip()
def _compact_alnum(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())


def _heading_path(nodes: list[dict[str, Any]]) -> list[str]:
    for node in nodes:
        path = node.get("heading_path") or node.get("lineage") or []
        if isinstance(path, list):
            return [str(part) for part in path if str(part).strip()]
    return []


def _heading_context(
    left_nodes: list[dict[str, Any]],
    right_nodes: list[dict[str, Any]],
) -> dict[str, list[str]]:
    return {
        "before": _heading_path(left_nodes),
        "after": _heading_path(right_nodes),
    }
