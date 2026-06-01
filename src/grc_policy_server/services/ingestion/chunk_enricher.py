"""Post-parse enrichment pipeline for ParsedChunk lists.

ChunkEnricher is a composable pass that runs AFTER Docling chunking has
produced ParsedChunk objects.  It adds
metadata that requires the full chunk list (language detection) or that should
be computed identically regardless of which extractor was used (section role,
clause semantics).

Usage:
    parsed_chunks = parse_docling_chunks(doc_json, raw_chunks)
    parsed_chunks = ChunkEnricher().enrich(parsed_chunks, docling_language=lang)

ParsedChunk is frozen — enrichment produces new instances via dataclasses.replace().
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Sequence

from grc_policy_server.services.comparison.policy_semantics import meaning_to_metadata
from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk

# ---------------------------------------------------------------------------
# Section role detection
# ---------------------------------------------------------------------------

_INFORMATIVE_ROLE_RE = re.compile(
    r"\b(?:annex|appendix|informative|foreword|preface|note|example|"
    r"anhang|hinweis|beispiel|anmerkung|erläuterung)\b",
    re.IGNORECASE,
)
_NORMATIVE_ROLE_RE = re.compile(
    r"\b(?:requirement|shall|normative|scope|general|"
    r"anforderung|normativ|anwendungsbereich)\b",
    re.IGNORECASE,
)


def _detect_section_role(section_path: tuple[str, ...] | list[str]) -> str:
    """Return 'informative' or 'normative' based on the section heading path.

    Scans from innermost heading outward; first match wins.  Defaults to
    'normative' when no heading contains a recognizable role keyword.
    """
    for heading in reversed(list(section_path)):
        if _INFORMATIVE_ROLE_RE.search(heading):
            return "informative"
        if _NORMATIVE_ROLE_RE.search(heading):
            return "normative"
    return "normative"


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_LANG_LEXICONS: dict[str, set[str]] = {
    "en": {
        "the", "and", "shall", "must", "should", "policy", "document",
        "requirements", "control", "controls", "access", "security", "is", "are",
    },
    "de": {
        "der", "die", "das", "und", "nicht", "mit", "sind", "muss", "müssen",
        "soll", "sollen", "richtlinie", "dokument", "anforderungen", "zugriff",
        "sicherheit",
    },
    "fr": {
        "le", "la", "les", "et", "pas", "avec", "sont", "doit", "doivent",
        "politique", "document", "exigences", "accès", "securite", "sécurité",
        "conformité",
    },
}


def _detect_language_rule_based(chunks: Sequence[ParsedChunk]) -> str:
    """Detect document language from the first few chunks via lexicon scoring."""
    sample_texts: list[str] = []
    for chunk in chunks[:5]:
        text = (chunk.text or "").strip()
        if text:
            sample_texts.append(text)
        if len(" ".join(sample_texts)) > 500:
            break
    if not sample_texts:
        return ""
    sample = " ".join(sample_texts)[:500].lower()
    tokens = re.findall(r"[a-zA-ZÀ-ÿ]+", sample)
    if not tokens:
        return ""
    scores: dict[str, int] = {code: 0 for code in _LANG_LEXICONS}
    for token in tokens:
        for code, lexicon in _LANG_LEXICONS.items():
            if token in lexicon:
                scores[code] += 1
    if any(ch in sample for ch in "äöüß"):
        scores["de"] += 2
    if any(ch in sample for ch in "àâçéèêëîïôûùüÿœæ"):
        scores["fr"] += 2
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    if scores[best] == 0:
        return ""
    winners = [code for code, score in scores.items() if score == scores[best]]
    return best if len(winners) == 1 else ""


# ---------------------------------------------------------------------------
# ChunkEnricher
# ---------------------------------------------------------------------------


class ChunkEnricher:
    """Applies enrichment passes to a list of ParsedChunk objects.

    Each pass is non-destructive: it calls dataclasses.replace() to produce
    new frozen instances rather than mutating existing ones.

    Passes (in order):
      1. Language detection — adds "detected_language" to all chunk metadata.
      2. Section role — adds "section_role" where not already set.
      3. Clause semantics — for clause chunks, adds obligation/meaning fields.
    """

    def enrich(
        self,
        chunks: list[ParsedChunk],
        *,
        docling_language: str = "",
    ) -> list[ParsedChunk]:
        language = docling_language or _detect_language_rule_based(chunks)
        enriched = list(chunks)

        for i, chunk in enumerate(enriched):
            metadata = dict(chunk.metadata)
            changed = False

            # Pass 1: language
            if language and "detected_language" not in metadata:
                metadata["detected_language"] = language
                changed = True

            # Pass 2: section role (setdefault — don't override explicit values
            # like "informative" set for footnote chunks during Docling parsing)
            if "section_role" not in metadata:
                metadata["section_role"] = _detect_section_role(chunk.section_path)
                changed = True

            # Pass 3: clause semantics
            if chunk.chunk_type == "clause":
                text = (chunk.text or "").strip()
                if text:
                    metadata.update(meaning_to_metadata(text))
                    metadata["semantic_source"] = "rule_based"
                    if language:
                        metadata["detected_language"] = language
                    changed = True

            if changed:
                enriched[i] = replace(chunk, metadata=metadata)

        return enriched
