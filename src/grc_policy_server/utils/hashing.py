from __future__ import annotations

import hashlib
import re
import unicodedata
from uuid import NAMESPACE_URL, uuid5

_WHITESPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
# Remove space between digit and unit symbol: "2 W" -> "2W", "100 MHz" -> "100MHz".
# Negative lookahead (?!\w{3,}) prevents matching long word suffixes like "5 polig".
_DIGIT_SPACE_UNIT_RE = re.compile(r"(\d)\s+([a-zA-Z])(?!\w{3,})")
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

    Handles:
    - Unicode normalization (NFKC) and soft-hyphen removal
    - Line-break hyphenation repair: "inter-\\nnational" -> "international"
    - Word-internal hyphens normalized to spaces: "EMV-Prüfung" == "EMV Prüfung"
    - Whitespace collapse
    - Single-letter unit symbols attached to digits: "2 W" -> "2W"
    - Bullet/numbered list prefix normalization
    - Punctuation spacing for :;,
    - Case normalization (lowercase)
    """
    text = (value or "").strip()
    text = _TRAILING_ESCAPE_RE.sub("", text)
    text = text.replace("­", "")  # soft hyphen
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFKC", text or "")
    # Repair line-break hyphenation BEFORE collapsing whitespace so \n is still present
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    # Collapse whitespace
    text = _WHITESPACE_RE.sub(" ", text)
    # Remove space between digit and single-letter unit symbol: "2 W" -> "2W"
    text = _DIGIT_SPACE_UNIT_RE.sub(r"\1\2", text)
    # Normalize bullets / numbered items
    text = BULLET_RE.sub("- ", text)
    # Normalize word-internal hyphens to spaces: "EMV-Prüfung" == "EMV Prüfung"
    text = re.sub(r"(?<=\w)-(?=\w)", " ", text)
    # Normalize punctuation spacing: one space after ;, (not . to preserve section
    # numbers like "5.2.1") and one space after :
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s*([;,])\s*", r"\1 ", text)
    text = re.sub(r"\s*:\s*", ": ", text)
    return text.lower().strip()


def slugify_text(value: str) -> str:
    normalized = _NON_WORD_RE.sub("-", normalize_text(value))
    return normalized.strip("-")


def stable_uuid(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, value))


def sort_by_page(list):
    return list["page_number"]


def normalize_whitespace(s: str) -> str:
    s = s.replace(" ", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s
