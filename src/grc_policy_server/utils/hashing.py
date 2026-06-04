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

# Subscript/superscript preservation maps.
# Applied BEFORE unicodedata.normalize("NFKC") because NFKC converts these
# Unicode characters to their ASCII base equivalents (e.g., ² → 2, ₂ → 2),
# silently making m² == m³ and CO₂ == CO₃ — critical in EMC/safety thresholds.
# We preserve them as ^N (superscript) and _N (subscript) ASCII marker notation.
_SUPERSCRIPT_TO_ASCII: dict[str, str] = {
    "⁰": "^0", "¹": "^1", "²": "^2", "³": "^3",
    "⁴": "^4", "⁵": "^5", "⁶": "^6", "⁷": "^7",
    "⁸": "^8", "⁹": "^9",
    "²": "^2",  # ² U+00B2
    "³": "^3",  # ³ U+00B3
    "¹": "^1",  # ¹ U+00B9
}
_SUBSCRIPT_TO_ASCII: dict[str, str] = {
    "₀": "_0", "₁": "_1", "₂": "_2", "₃": "_3",
    "₄": "_4", "₅": "_5", "₆": "_6", "₇": "_7",
    "₈": "_8", "₉": "_9",
}
_SUB_SUPER_TABLE = str.maketrans({**_SUPERSCRIPT_TO_ASCII, **_SUBSCRIPT_TO_ASCII})
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
    # Preserve subscript/superscript BEFORE NFKC strips them.
    # e.g. m² → m^2, CO₂ → CO_2 so they remain distinguishable after normalisation.
    text = text.translate(_SUB_SUPER_TABLE)
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


def pure_text_hash(text: str) -> str:
    """SHA256 of alphanumeric-only lowercase text.

    Strips whitespace, punctuation, and symbols so that cosmetic differences like
    "...occur." vs "...occur.." produce the same hash.

    IMPORTANT: Only use to skip diffs when meaning_change == "unchanged" has already
    been confirmed — stripping punctuation conflates "3.5" and "35", so numeric-limit
    changes must be guarded at the call site.
    """
    normalized = "".join(c for c in (text or "").lower() if c.isalnum())
    return sha256_hex(normalized.encode("utf-8")) if normalized else ""


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
