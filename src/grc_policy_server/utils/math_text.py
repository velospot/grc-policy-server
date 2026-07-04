"""Pure-text math canonicalization shared by hashing and ingestion.

Compliance documents encode the same physical quantity many ways depending on
authoring tool, locale, and OCR quality: ``30 dBµV/m`` vs ``30 dBμV/m`` vs
``30 db (uV/m)``, ``13,5 ± 0,5 kV`` vs ``13.5±0.5 kV``, ``3 × 10⁶`` vs
``3x10^6``. Comparison must treat these as equal, so canonicalization happens
before hashing/normalization lowercases and collapses whitespace.

Ordering constraint: superscript translation must run BEFORE any NFKC
normalization — NFKC flattens ``10⁶`` to ``106`` which silently changes the
value by orders of magnitude.
"""
from __future__ import annotations

import re

# Superscript characters → ASCII (sign + digits). Translated as a "^" run.
_SUPERSCRIPT_MAP = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁺": "+", "⁻": "-",
}
_SUPERSCRIPT_RUN_RE = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+")

# µ (U+00B5 MICRO SIGN) or μ (U+03BC GREEK MU) immediately before a unit letter.
_MICRO_UNIT_RE = re.compile(r"[µμ](?=[A-Za-zΩ])")

# Multiplication sign between numeric factors: "3 × 10^6" / "2·10^3" → "3x10^6".
# Surrounding whitespace is consumed so spaced and unspaced variants compare equal.
_MULT_SIGN_RE = re.compile(r"(\d)\s*[×·∙⋅]\s*(?=\d)")

# Decimal comma: "13,5" / "13, 5" → "13.5". Requires 1-2 trailing digits so
# English thousands separators ("1,000") are left untouched.
_DECIMAL_COMMA_RE = re.compile(r"(\d)\s*,\s*(\d{1,2})(?!\d)")

# Tolerance / plus-minus spacing: "13.5 ± 0.5" → "13.5±0.5".
_PLUS_MINUS_RE = re.compile(r"\s*±\s*")

# En/em dash between digits is a numeric range: "30 – 230 MHz" → "30-230 MHz".
_NUM_DASH_RE = re.compile(r"(?<=\d)\s*[–—]\s*(?=\d)")

# "dB (µV/m)" / "db( uV )" — PDF word-breaking and OCR parenthesize the
# micro-unit; collapse to the attached form so it equals "dBµV/m".
_DB_PAREN_UNIT_RE = re.compile(r"\bd[bB]\s*\(\s*([uµμ][VvAa](?:\s*/\s*m)?)\s*\)")

# Unit fraction spacing: "dBuV / m" → "dBuV/m".
_UNIT_SLASH_RE = re.compile(r"(?<=[A-Za-z])\s*/\s*(?=[A-Za-z])")

# Value/unit spacing: "30 dBuV/m" == "30dBuV/m", "150 kHz" == "150kHz".
# Restricted to dB tokens and SI-prefixed unit tokens so prose is untouched.
_VALUE_UNIT_RE = re.compile(
    r"(\d)\s+("
    r"d[Bb](?:[uµμ]?[VvAa])?(?:/m)?\b"
    r"|[kKMGmµun]?(?:Hz|V|A|W|F|H|T)\b"
    r"|ohm\b"
    r")"
)

_OHM_RE = re.compile(r"[ΩΩ]")


def normalize_math_text(text: str) -> str:
    """Canonicalize mathematical notation in plain text.

    Safe on prose: every rule is anchored to digits, unit letters, or dedicated
    math symbols. Idempotent. Must run before NFKC normalization.
    """
    if not text:
        return text
    result = _SUPERSCRIPT_RUN_RE.sub(
        lambda m: "^" + "".join(_SUPERSCRIPT_MAP.get(ch, ch) for ch in m.group(0)),
        text,
    )
    result = _MICRO_UNIT_RE.sub("u", result)
    result = _OHM_RE.sub("ohm", result)
    result = _DB_PAREN_UNIT_RE.sub(lambda m: "dB" + re.sub(r"\s+", "", m.group(1)), result)
    result = _UNIT_SLASH_RE.sub("/", result)
    result = _VALUE_UNIT_RE.sub(lambda m: m.group(1) + re.sub(r"\s+", "", m.group(2)), result)
    result = _MULT_SIGN_RE.sub(r"\1x", result)
    result = _DECIMAL_COMMA_RE.sub(r"\1.\2", result)
    result = _PLUS_MINUS_RE.sub("±", result)
    result = _NUM_DASH_RE.sub("-", result)
    return result


_LATEX_GARBAGE_RE = re.compile(r"[�]|\\\\[a-zA-Z]{20,}")
_LATEX_NUMERIC_RE = re.compile(r"\d")


def score_formula_confidence(latex: str) -> float:
    """Heuristic extraction confidence for a docling formula-enrichment string.

    Factors: balanced braces/brackets (structure survived), presence of any
    numeric or symbolic content (not an empty/failed enrichment), absence of
    replacement characters or absurd macro runs (OCR/enrichment garbage).
    """
    text = (latex or "").strip()
    if not text:
        return 0.0
    score = 1.0
    if text.count("{") != text.count("}"):
        score -= 0.35
    if text.count("(") != text.count(")"):
        score -= 0.15
    if _LATEX_GARBAGE_RE.search(text):
        score -= 0.35
    if not _LATEX_NUMERIC_RE.search(text) and "\\" not in text:
        # Neither numbers nor LaTeX macros — likely mis-labelled prose.
        score -= 0.25
    return max(round(score, 3), 0.0)
