from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from grc_policy_server.utils.hashing import normalize_text, normalize_whitespace

OBLIGATION_STRENGTH = {
    "may": 0,
    "should": 1,
    "recommended": 2,
    "required": 3,
    "must": 4,
    "shall": 5,
}

_CONDITION_RE = re.compile(
    r"\b(if|when|unless|except when|provided that|subject to|where)\b",
    re.IGNORECASE,
)
_OBLIGATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("shall", re.compile(r"\bshall\b", re.IGNORECASE)),
    ("must", re.compile(r"\bmust\b", re.IGNORECASE)),
    (
        "required",
        re.compile(
            r"\b(?:is|are|be|been)?\s*required(?:\s+to)?\b|\brequired\s+to\b",
            re.IGNORECASE,
        ),
    ),
    (
        "recommended",
        re.compile(
            r"\b(?:is|are|be|been)?\s*recommended(?:\s+to)?\b|\brecommended\s+to\b",
            re.IGNORECASE,
        ),
    ),
    ("should", re.compile(r"\bshould\b", re.IGNORECASE)),
    ("may", re.compile(r"\bmay\b", re.IGNORECASE)),
)
_ENUMERATION_PREFIX_RE = re.compile(
    r"^\s*(?:(?:section|clause|article|appendix|annex)\s+)?[a-z]?\d+(?:\.\d+)*[a-z]?(?:[.):]|\s)\s*",
    re.IGNORECASE,
)
_CITATION_RE = re.compile(r"(?<!\w)\[\d+\](?!\w)")
_PAREN_MARKER_RE = re.compile(r"(?<!\w)\((?:[a-z]|\d+|[ivxlcdm]+)\)(?!\w)", re.IGNORECASE)
_NOISE_RE = re.compile(r"^[\d\W_]+$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "all",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "must",
    "of",
    "or",
    "shall",
    "should",
    "that",
    "the",
    "their",
    "to",
    "use",
    "using",
    "with",
}
_CANONICAL_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bmulti[-\s]?factor authentication\b", re.IGNORECASE),
        "mfa",
    ),
    (
        re.compile(r"\btwo[-\s]?factor authentication\b", re.IGNORECASE),
        "mfa",
    ),
    (re.compile(r"\b2fa\b", re.IGNORECASE), "mfa"),
    (re.compile(r"\bprivileged accounts\b", re.IGNORECASE), "privileged access"),
    (re.compile(r"\bprivileged account\b", re.IGNORECASE), "privileged access"),
    (re.compile(r"\badmins\b", re.IGNORECASE), "administrators"),
    (re.compile(r"\badmin\b", re.IGNORECASE), "administrator"),
)


@dataclass(frozen=True)
class ClauseMeaning:
    obligation: str
    subject: str
    action: str
    object: str
    condition: str


@dataclass(frozen=True)
class MeaningComparison:
    score: float
    obligation_change: str
    obligation_delta: int


def clean_policy_text(value: str) -> str:
    text = normalize_whitespace(value or "")
    if not text:
        return ""
    text = _CITATION_RE.sub(" ", text)
    text = _PAREN_MARKER_RE.sub(" ", text)
    text = _ENUMERATION_PREFIX_RE.sub("", text)
    text = re.sub(r"[.]{3,}", " ", text)
    text = re.sub(r"[-_=]{3,}", " ", text)
    for pattern, replacement in _CANONICAL_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return normalize_text(text)


def is_noise_text(value: str) -> bool:
    text = normalize_whitespace(value or "")
    if len(text) < 5:
        return True
    return bool(_NOISE_RE.fullmatch(text))


def ends_with_terminal_punctuation(value: str) -> bool:
    return bool((value or "").rstrip().endswith((".", "!", "?", ":", ";")))


def starts_with_lowercase(value: str) -> bool:
    for char in (value or "").lstrip():
        if char.isalpha():
            return char.islower()
    return False


def extract_clause_meaning(value: str) -> ClauseMeaning:
    clean_text = clean_policy_text(value)
    if not clean_text:
        return ClauseMeaning("", "", "", "", "")

    statement, condition = _split_condition(clean_text)
    obligation, match_start, match_end = _detect_obligation(statement)
    if not obligation:
        return ClauseMeaning("", _trim_phrase(statement), "", "", condition)

    subject = _trim_phrase(statement[:match_start])
    predicate = _trim_phrase(statement[match_end:])
    action, obj = _split_predicate(predicate)

    if not subject and obj.startswith("for "):
        subject = _trim_phrase(obj[4:])
        obj = ""

    return ClauseMeaning(obligation, subject, action, obj, condition)


def compare_clause_meaning(left: ClauseMeaning, right: ClauseMeaning) -> MeaningComparison:
    subject_score = token_overlap(left.subject, right.subject)
    action_score = 1.0 if left.action and left.action == right.action else token_overlap(
        left.action, right.action
    )
    object_score = token_overlap(left.object, right.object)
    condition_score = token_overlap(left.condition, right.condition)

    populated_scores = [
        score
        for score, left_value, right_value in (
            (subject_score, left.subject, right.subject),
            (action_score, left.action, right.action),
            (object_score, left.object, right.object),
            (condition_score, left.condition, right.condition),
        )
        if left_value or right_value
    ]
    score = sum(populated_scores) / len(populated_scores) if populated_scores else 0.0
    delta = obligation_rank(right.obligation) - obligation_rank(left.obligation)
    return MeaningComparison(
        score=score,
        obligation_change=describe_obligation_change(left.obligation, right.obligation),
        obligation_delta=delta,
    )


def token_overlap(left: str, right: str) -> float:
    left_tokens = set(tokenize_policy_text(left))
    right_tokens = set(tokenize_policy_text(right))
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return intersection / union if union else 0.0


def tokenize_policy_text(value: str) -> list[str]:
    tokens = [
        token
        for token in _TOKEN_RE.findall(clean_policy_text(value))
        if len(token) > 1 and token not in _STOPWORDS
    ]
    return tokens


def obligation_rank(obligation: str) -> int:
    return OBLIGATION_STRENGTH.get((obligation or "").lower(), -1)


def describe_obligation_change(left: str, right: str) -> str:
    left_rank = obligation_rank(left)
    right_rank = obligation_rank(right)
    if left_rank == right_rank:
        return "unchanged"
    if left_rank < 0 and right_rank >= 0:
        return "added"
    if left_rank >= 0 and right_rank < 0:
        return "removed"
    if right_rank > left_rank:
        return "strengthened"
    return "weakened"


def meaning_to_metadata(value: str) -> dict[str, str]:
    meaning = extract_clause_meaning(value)
    return {
        "clean_text": clean_policy_text(value),
        "obligation": meaning.obligation,
        "subject": meaning.subject,
        "action": meaning.action,
        "object": meaning.object,
        "condition": meaning.condition,
    }


def semantic_signature(value: str) -> str:
    meaning = extract_clause_meaning(value)
    parts = [meaning.subject, meaning.action, meaning.object, meaning.condition]
    return " | ".join(part for part in parts if part)


def average_token_overlap(values: Iterable[str], other_values: Iterable[str]) -> float:
    left = " ".join(value for value in values if value)
    right = " ".join(value for value in other_values if value)
    return token_overlap(left, right)


def _split_condition(text: str) -> tuple[str, str]:
    match = _CONDITION_RE.search(text)
    if not match:
        return text, ""
    start = match.start()
    return _trim_phrase(text[:start]), _trim_phrase(text[start:])


def _detect_obligation(text: str) -> tuple[str, int, int]:
    candidates: list[tuple[int, int, str]] = []
    for label, pattern in _OBLIGATION_PATTERNS:
        match = pattern.search(text)
        if match:
            candidates.append((match.start(), match.end(), label))
    if not candidates:
        return "", 0, 0
    candidates.sort(key=lambda item: item[0])
    start, end, label = candidates[0]
    return label, start, end


def _split_predicate(text: str) -> tuple[str, str]:
    predicate = _trim_phrase(text)
    if not predicate:
        return "", ""

    parts = predicate.split(maxsplit=1)
    action = parts[0]
    obj = parts[1] if len(parts) > 1 else ""

    if action in {"be", "is", "are"} and obj:
        nested = obj.split(maxsplit=1)
        action = nested[0]
        obj = nested[1] if len(nested) > 1 else ""

    action = _lemmatize_action(action)
    return action, _trim_phrase(obj)


def _trim_phrase(value: str) -> str:
    trimmed = (value or "").strip(" ,;:.")
    trimmed = re.sub(r"^(all|the|a|an)\s+", "", trimmed)
    return trimmed.strip()


def _lemmatize_action(value: str) -> str:
    action = value.strip()
    if action.endswith("ied") and len(action) > 4:
        return action[:-3] + "y"
    if action.endswith("ing") and len(action) > 5:
        return action[:-3]
    if action.endswith("ed") and len(action) > 4:
        return action[:-2]
    if action.endswith("es") and len(action) > 4:
        return action[:-2]
    if action.endswith("s") and len(action) > 3:
        return action[:-1]
    return action
