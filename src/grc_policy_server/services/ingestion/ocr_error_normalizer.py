"""Normalization of systematic OCR substitution errors (GAP-I6).

Applied only to text that actually came from OCR (``ocr``/``ocr_used``
metadata), never to native PDF text — the rules are aggressive inside numeric
contexts and would corrupt born-digital content like part numbers.

Rule sources: evaluation_results/02_gap_analysis_and_improvement_plan.md and
observed Tesseract errors on DE/EN compliance PDFs (3O→30, l50→150,
rnuss→muss, lnitial→Initial, µV spacing variants).
"""
from __future__ import annotations

import re

# --- Digit-context confusions -------------------------------------------------
# Letter O inside or adjacent to digits is a zero: 3O→30, O5→05, 1O0→100.
_O_BETWEEN_DIGITS_RE = re.compile(r"(?<=\d)[Oo](?=\d)")
_O_AFTER_DIGIT_RE = re.compile(r"(?<=\d)O\b")
# Lowercase l or uppercase I directly before digits is a one: l50→150, I20→120.
_L_BEFORE_DIGITS_RE = re.compile(r"\b[lI](?=\d{2,}\b)")
_L_BETWEEN_DIGITS_RE = re.compile(r"(?<=\d)[lI](?=\d)")
# Letter S adjacent to digits inside a number: 1S0→150 (conservative: between digits only).
_S_BETWEEN_DIGITS_RE = re.compile(r"(?<=\d)S(?=\d)")

# --- Word-level confusions (rn ↔ m, ln ↔ In) ----------------------------------
# Exact-word fixes only; a generic rn→m rule would corrupt German words like
# "gern" or English "burn".
_WORD_FIXES: dict[str, str] = {
    "rnuss": "muss",
    "rnüssen": "müssen",
    "rnusste": "musste",
    "lnitial": "Initial",
    "lnformation": "Information",
    "lnstallation": "Installation",
    "rnax": "max",
    "rnin": "min",
}
_WORD_FIX_RE = re.compile(
    r"\b(" + "|".join(re.escape(word) for word in _WORD_FIXES) + r")\b"
)

# --- Unit spacing artefacts ---------------------------------------------------
# OCR splits units from values and parenthesizes them oddly: "30 dB (µV/m)"
# and "dB( µV )" both mean dBµV/m. Collapse space inside the parens and
# between dB and the parenthesized micro-unit.
_DB_PAREN_UNIT_RE = re.compile(
    r"\bd[bB]\s*\(\s*([µμu][VvAa](?:\s*/\s*m)?)\s*\)",
)


def normalize_ocr_errors(text: str) -> str:
    """Fix systematic OCR substitution errors. Idempotent, OCR text only."""
    if not text:
        return text
    result = _O_BETWEEN_DIGITS_RE.sub("0", text)
    result = _O_AFTER_DIGIT_RE.sub("0", result)
    result = _L_BEFORE_DIGITS_RE.sub("1", result)
    result = _L_BETWEEN_DIGITS_RE.sub("1", result)
    result = _S_BETWEEN_DIGITS_RE.sub("5", result)
    result = _WORD_FIX_RE.sub(lambda m: _WORD_FIXES[m.group(1)], result)
    result = _DB_PAREN_UNIT_RE.sub(
        lambda m: "dB" + re.sub(r"\s+", "", m.group(1)), result
    )
    return result
