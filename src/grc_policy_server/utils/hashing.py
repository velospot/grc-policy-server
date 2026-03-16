from __future__ import annotations

import hashlib
import re
from uuid import NAMESPACE_URL, uuid5

_WHITESPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
# Pattern for space between digit and letter (unit pattern): "2 W" -> "2W"
_DIGIT_SPACE_UNIT_RE = re.compile(r"(\d)\s+([a-zA-Z])")
# Pattern for trailing escape characters
_TRAILING_ESCAPE_RE = re.compile(r"[\\\/]+$")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", (value or "").strip()).lower()


def normalize_for_comparison(value: str) -> str:
    """Normalize text for comparison, removing cosmetic differences.

    Handles:
    - Whitespace normalization (collapse multiple spaces)
    - Case normalization (lowercase)
    - Space between number and unit: "2 W" -> "2W"
    - Trailing escape characters: "text\\" -> "text"
    """
    text = (value or "").strip()
    # Remove trailing escape characters (PDF artifacts)
    text = _TRAILING_ESCAPE_RE.sub("", text)
    # Collapse whitespace
    text = _WHITESPACE_RE.sub(" ", text)
    # Remove space between digit and unit letter: "2 W" -> "2W"
    text = _DIGIT_SPACE_UNIT_RE.sub(r"\1\2", text)
    # Lowercase for case-insensitive comparison
    return text.lower().strip()


def slugify_text(value: str) -> str:
    normalized = _NON_WORD_RE.sub("-", normalize_text(value))
    return normalized.strip("-")


def stable_uuid(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, value))


def sort_by_page(list):
    return list["page_number"]


def normalize_whitespace(s: str) -> str:
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s
