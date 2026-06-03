"""Language-keyed obligation pattern registry for multilingual documents.

Organises OBLIGATION_PATTERNS from policy_semantics.py into per-language
buckets so callers can retrieve only the relevant patterns for a known
document language, improving precision for non-English documents.

Backward-compatible: existing code that imports OBLIGATION_PATTERNS directly
continues to work — this module only *adds* a structured view on top.
"""
from __future__ import annotations

import re
from typing import Tuple

# Pattern tuple type: (obligation_key, compiled_regex)
PatternTuple = Tuple[str, re.Pattern]

# ---------------------------------------------------------------------------
# Language-specific pattern sets (same regex objects as policy_semantics.py)
# ---------------------------------------------------------------------------

_EN: tuple[PatternTuple, ...] = (
    ("shall_not", re.compile(r"\bshall\s+not\b", re.IGNORECASE)),
    ("must_not",  re.compile(r"\bmust\s+not\b", re.IGNORECASE)),
    ("must_not",  re.compile(r"\b(?:prohibited|forbidden|not\s+permitted)\b", re.IGNORECASE)),
    ("shall",     re.compile(r"\bshall\b", re.IGNORECASE)),
    ("must",      re.compile(r"\bmust\b", re.IGNORECASE)),
    ("required",  re.compile(
        r"\b(?:is|are|be|been)?\s*required(?:\s+to)?\b|\brequired\s+to\b",
        re.IGNORECASE,
    )),
    ("recommended", re.compile(
        r"\b(?:is|are|be|been)?\s*recommended(?:\s+to)?\b|\brecommended\s+to\b",
        re.IGNORECASE,
    )),
    ("should",    re.compile(r"\bshould\b", re.IGNORECASE)),
    ("may",       re.compile(r"\bmay\b", re.IGNORECASE)),
)

_DE: tuple[PatternTuple, ...] = (
    ("must_not",  re.compile(r"\bdarf\s+nicht\b|\bdürfen\s+nicht\b", re.IGNORECASE)),
    ("shall",     re.compile(r"\bmuss\b|\bmüssen\b", re.IGNORECASE)),
    ("shall",     re.compile(r"\bsoll\b|\bsollen\b", re.IGNORECASE)),
    ("required",  re.compile(r"\berforderlich\b|\bnotwendig\b", re.IGNORECASE)),
    ("recommended", re.compile(r"\bempfohlen\b|\bempfiehlt\b", re.IGNORECASE)),
    ("may",       re.compile(r"\bdarf\b|\bdürfen\b", re.IGNORECASE)),
    ("may",       re.compile(r"\bkann\b|\bkönnen\b", re.IGNORECASE)),
)

_FR: tuple[PatternTuple, ...] = (
    ("must_not",  re.compile(r"\bne\s+doit\s+pas\b|\bne\s+doivent\s+pas\b", re.IGNORECASE)),
    ("shall",     re.compile(r"\bdoit\b|\bdoivent\b", re.IGNORECASE)),
    ("shall",     re.compile(r"\bdevra\b|\bdevront\b", re.IGNORECASE)),
    ("required",  re.compile(r"\brequis\b|\bexigé\b|\bobligatoire\b", re.IGNORECASE)),
    ("recommended", re.compile(r"\brecommandé\b|\bconseillé\b", re.IGNORECASE)),
    ("should",    re.compile(r"\bdevrait\b|\bdevraient\b", re.IGNORECASE)),
    ("may",       re.compile(r"\bpeut\b|\bpeuvent\b", re.IGNORECASE)),
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_LANG_MAP: dict[str, tuple[PatternTuple, ...]] = {
    "en": _EN,
    "de": _DE,
    "fr": _FR,
}

# Combined set used when language is unknown (backward-compatible ordering:
# negations first, then stronger obligations, then weaker).
_ALL: tuple[PatternTuple, ...] = (
    *_EN[:3],   # shall_not, must_not, must_not (prohibited) — EN negations first
    *_DE[:1],   # darf nicht — DE negation
    *_FR[:1],   # ne doit pas — FR negation
    *_EN[3:],   # shall, must, required, recommended, should, may
    *_DE[1:],   # remaining DE
    *_FR[1:],   # remaining FR
)


class ObligationPatternRegistry:
    """Static registry of obligation patterns organised by document language."""

    @staticmethod
    def all_patterns() -> tuple[PatternTuple, ...]:
        """Return the full multilingual set — same as OBLIGATION_PATTERNS."""
        return _ALL

    @staticmethod
    def for_language(language: str) -> tuple[PatternTuple, ...]:
        """Return patterns for a specific language code.

        Falls back to the English set for unknown languages, and to the full
        multilingual set when language is empty.
        """
        if not language:
            return _ALL
        lang = language.split("-")[0].lower()  # "de-DE" → "de"
        return _LANG_MAP.get(lang, _EN)

    @staticmethod
    def extract_obligation(text: str, language: str = "") -> str | None:
        """Return the first matched obligation key for *text*, or None.

        Uses language-specific patterns when a language code is supplied,
        otherwise falls back to the full multilingual set.
        """
        patterns = ObligationPatternRegistry.for_language(language)
        for key, pattern in patterns:
            if pattern.search(text):
                return key
        return None
