from __future__ import annotations

import hashlib
import re
import unicodedata
from uuid import NAMESPACE_URL, uuid5

_WHITESPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
# Pattern for space between digit and letter (unit pattern): "2 W" -> "2W"
_DIGIT_SPACE_UNIT_RE = re.compile(r"(\d)\s+([a-zA-Z])")
# Pattern for trailing escape characters
_TRAILING_ESCAPE_RE = re.compile(r"[\\\/]+$")
BULLET_RE = re.compile(
    r"^\s*([•●▪◦\-–—*]|[\(\[]?\d+[\)\].:]|[A-Za-z]\))\s+",
    re.M,
)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", (value or "").strip()).lower()


def normalize_for_comparison(value: str) -> str:
    """Normalize text for comparison, removing cosmetic differences.
    Lossless-ish canonicalization for comparison:
    - normalize unicode
    - repair line-break hyphenation
    - normalize bullets/numbering
    - collapse spacing
    - keep lexical meaning intact

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
    text = text.replace("\u00ad", "")  # soft hyphen
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFKC", text or "")
    # inter-\nnational -> international
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)

    # normalize bullets / numbered items
    text = BULLET_RE.sub("- ", text)

    # normalize punctuation spacing without removing punctuation
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s*([:;,.\-])\s*", r"\1 ", text)
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
