from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from grc_policy_server.utils.hashing import normalize_text, normalize_whitespace

OBLIGATION_STRENGTH = {
    # English
    "may": 0,
    "should": 1,
    "recommended": 2,
    "required": 3,
    "must": 4,
    "shall": 5,
    "must_not": 4,
    "shall_not": 5,
    # German aliases
    "kann": 0,  # kann = may
    "darf": 0,  # darf = may (permission)
    "sollte": 1,  # sollte = should
    "soll": 5,  # soll = shall (strong in German policy)
    "empfohlen": 2,  # empfohlen = recommended
    "erforderlich": 3,  # erforderlich = required
    "muss": 4,  # muss = must
    "müssen": 4,  # müssen = must (plural)
    "darf_nicht": 4,  # darf nicht = must not
    # French aliases
    "peut": 0,  # peut = may
    "devrait": 1,  # devrait = should
    "recommandé": 2,  # recommandé = recommended
    "requis": 3,  # requis = required
    "exigé": 3,  # exigé = required
    "doit": 5,  # doit = shall/must
    "doivent": 5,  # doivent = shall/must (plural)
    "devra": 5,  # devra = shall (future)
    "ne_doit_pas": 4,  # ne doit pas = must not
}

_CONDITION_RE = re.compile(
    r"\b("
    # English
    r"if|when|unless|except when|provided that|subject to|where|"
    # German
    r"wenn|falls|sofern|außer wenn|es sei denn|unter der Bedingung|vorausgesetzt|"
    # French
    r"si|lorsque|sauf si|à condition que|sous réserve|pourvu que"
    r")\b",
    re.IGNORECASE,
)

_OBLIGATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # English
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
    # German - Negations first (more specific)
    ("must_not", re.compile(r"\bdarf\s+nicht\b|\bdürfen\s+nicht\b", re.IGNORECASE)),
    ("shall", re.compile(r"\bmuss\b|\bmüssen\b", re.IGNORECASE)),  # muss = must/shall
    (
        "shall",
        re.compile(r"\bsoll\b|\bsollen\b", re.IGNORECASE),
    ),  # soll = shall/should (strong in German policy)
    ("required", re.compile(r"\berforderlich\b|\bnotwendig\b", re.IGNORECASE)),
    ("recommended", re.compile(r"\bempfohlen\b|\bempfiehlt\b", re.IGNORECASE)),
    (
        "may",
        re.compile(r"\bdarf\b|\bdürfen\b", re.IGNORECASE),
    ),  # darf = may (permission)
    ("may", re.compile(r"\bkann\b|\bkönnen\b", re.IGNORECASE)),  # kann = can/may
    # French - Negations first (more specific)
    (
        "must_not",
        re.compile(r"\bne\s+doit\s+pas\b|\bne\s+doivent\s+pas\b", re.IGNORECASE),
    ),
    ("shall", re.compile(r"\bdoit\b|\bdoivent\b", re.IGNORECASE)),  # doit = must/shall
    (
        "shall",
        re.compile(r"\bdevra\b|\bdevront\b", re.IGNORECASE),
    ),  # devra = shall (future)
    ("required", re.compile(r"\brequis\b|\bexigé\b|\bobligatoire\b", re.IGNORECASE)),
    ("recommended", re.compile(r"\brecommandé\b|\bconseillé\b", re.IGNORECASE)),
    ("may", re.compile(r"\bpeut\b|\bpeuvent\b", re.IGNORECASE)),  # peut = may/can
)

_ENUMERATION_PREFIX_RE = re.compile(
    r"^\s*(?:(?:section|clause|article|appendix|annex)\s+)?[a-z]?\d+(?:\.\d+)*[a-z]?(?:[.):]|\s)\s*",
    re.IGNORECASE,
)
_CITATION_RE = re.compile(r"(?<!\w)\[\d+\](?!\w)")
_PAREN_MARKER_RE = re.compile(
    r"(?<!\w)\((?:[a-z]|\d+|[ivxlcdm]+)\)(?!\w)", re.IGNORECASE
)
_NOISE_RE = re.compile(r"^[\d\W_]+$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS_EN = {
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
    "of",
    "or",
    "that",
    "the",
    "their",
    "to",
    "use",
    "using",
    "with",
    "this",
    "these",
    "those",
    "it",
    "its",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "can",
    "could",
    "into",
    "on",
    "upon",
    "such",
}
_STOPWORDS_DE = {
    "der",
    "die",
    "das",
    "den",
    "dem",
    "des",
    "ein",
    "eine",
    "einer",
    "einem",
    "einen",
    "und",
    "oder",
    "aber",
    "als",
    "auch",
    "auf",
    "aus",
    "bei",
    "bis",
    "durch",
    "fuer",
    "gegen",
    "im",
    "in",
    "ist",
    "mit",
    "nach",
    "nicht",
    "noch",
    "nur",
    "ob",
    "ohne",
    "so",
    "sowie",
    "ueber",
    "um",
    "und",
    "unter",
    "vom",
    "von",
    "vor",
    "wenn",
    "wie",
    "wird",
    "zu",
    "zum",
    "zur",
    "sind",
    "sein",
    "seine",
    "seiner",
    "war",
    "waren",
    "wurde",
    "wurden",
    "werden",
    "hat",
    "haben",
    "kann",
}
_STOPWORDS_FR = {
    "le",
    "la",
    "les",
    "un",
    "une",
    "des",
    "du",
    "de",
    "et",
    "ou",
    "mais",
    "donc",
    "car",
    "ni",
    "ce",
    "cette",
    "ces",
    "son",
    "sa",
    "ses",
    "leur",
    "leurs",
    "au",
    "aux",
    "par",
    "pour",
    "sur",
    "sous",
    "avec",
    "sans",
    "dans",
    "en",
    "est",
    "sont",
    "etre",
    "avoir",
    "fait",
    "faire",
    "peut",
    "peuvent",
    "doit",
    "qui",
    "que",
    "quoi",
    "dont",
    "nous",
    "vous",
    "ils",
    "elles",
    "se",
    "ne",
    "pas",
    "plus",
    "tout",
    "tous",
    "toute",
    "toutes",
    "ete",
    "sera",
    "seront",
}
# Note: "must", "shall", "should", "muss", "soll", "doit" intentionally excluded - critical for policy comparison


def get_stopwords(language: str = "") -> set[str]:
    """Get stopwords for the specified language."""
    if language == "de":
        return _STOPWORDS_DE
    if language == "fr":
        return _STOPWORDS_FR
    if language == "en":
        return _STOPWORDS_EN
    # For unknown language, combine all to be safe
    return _STOPWORDS_EN | _STOPWORDS_DE | _STOPWORDS_FR


_UNICODE_PUNCT_TRANSLATION = {
    ord("“"): '"',
    ord("”"): '"',
    ord("‘"): "'",
    ord("’"): "'",
    ord("–"): "-",
    ord("—"): "-",
    ord("…"): "...",
}
_CANONICAL_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    # English MFA variants
    (
        re.compile(r"\bmulti[-\s]?factor authentication\b", re.IGNORECASE),
        "mfa",
    ),
    (
        re.compile(r"\btwo[-\s]?factor authentication\b", re.IGNORECASE),
        "mfa",
    ),
    (re.compile(r"\b2fa\b", re.IGNORECASE), "mfa"),
    # German MFA variants
    (re.compile(r"\bmehr[-\s]?faktor[-\s]?authentifizierung\b", re.IGNORECASE), "mfa"),
    (re.compile(r"\bzwei[-\s]?faktor[-\s]?authentifizierung\b", re.IGNORECASE), "mfa"),
    # French MFA variants
    (
        re.compile(
            r"\bauthentification\s+(?:à\s+)?multi[-\s]?facteurs?\b", re.IGNORECASE
        ),
        "mfa",
    ),
    (
        re.compile(r"\bauthentification\s+(?:à\s+)?deux\s+facteurs?\b", re.IGNORECASE),
        "mfa",
    ),
    # Privileged access
    (re.compile(r"\bprivileged accounts\b", re.IGNORECASE), "privileged access"),
    (re.compile(r"\bprivileged account\b", re.IGNORECASE), "privileged access"),
    (
        re.compile(r"\bprivilegierte\s+zugriffe?\b", re.IGNORECASE),
        "privileged access",
    ),  # German
    (
        re.compile(r"\baccès\s+privilégiés?\b", re.IGNORECASE),
        "privileged access",
    ),  # French
    # Administrators
    (re.compile(r"\badmins\b", re.IGNORECASE), "administrators"),
    (re.compile(r"\badmin\b", re.IGNORECASE), "administrator"),
    (re.compile(r"\badministratoren\b", re.IGNORECASE), "administrators"),  # German
    (re.compile(r"\badministrateurs?\b", re.IGNORECASE), "administrator"),  # French
    # Users
    (re.compile(r"\bend[-\s]?users?\b", re.IGNORECASE), "users"),
    (re.compile(r"\bbenutzer\b", re.IGNORECASE), "users"),  # German
    (re.compile(r"\butilisateurs?\b", re.IGNORECASE), "users"),  # French
    # Staff/Personnel
    (re.compile(r"\bpersonnel\b", re.IGNORECASE), "staff"),
    (re.compile(r"\bstaff\b", re.IGNORECASE), "staff"),
    (re.compile(r"\bmitarbeiter\b", re.IGNORECASE), "staff"),  # German
    (re.compile(r"\bpersonnels?\b", re.IGNORECASE), "staff"),  # French
    # Third-party
    (re.compile(r"\bthird[-\s]?parties\b", re.IGNORECASE), "third-party"),
    (re.compile(r"\bthird[-\s]?party\b", re.IGNORECASE), "third-party"),
    (re.compile(r"\bvendors?\b", re.IGNORECASE), "third-party"),
    (re.compile(r"\bservice providers?\b", re.IGNORECASE), "third-party"),
    (re.compile(r"\bdrittanbieter\b", re.IGNORECASE), "third-party"),  # German
    (re.compile(r"\bdienstleister\b", re.IGNORECASE), "third-party"),  # German
    (re.compile(r"\btiers\b", re.IGNORECASE), "third-party"),  # French
    (re.compile(r"\bprestataires?\b", re.IGNORECASE), "third-party"),  # French
    (re.compile(r"\bfournisseurs?\b", re.IGNORECASE), "third-party"),  # French
    # PII
    (
        re.compile(r"\bpersonally identifiable information\b", re.IGNORECASE),
        "pii",
    ),
    (re.compile(r"\bpii\b", re.IGNORECASE), "pii"),
    (re.compile(r"\bpersonenbezogene\s+daten\b", re.IGNORECASE), "pii"),  # German
    (re.compile(r"\bdonnées\s+personnelles\b", re.IGNORECASE), "pii"),  # French
    (
        re.compile(r"\bdonnées\s+à\s+caractère\s+personnel\b", re.IGNORECASE),
        "pii",
    ),  # French GDPR
    # SSN
    (re.compile(r"\bsocial security numbers?\b", re.IGNORECASE), "ssn"),
    (re.compile(r"\bssn\b", re.IGNORECASE), "ssn"),
    (re.compile(r"\bsozialversicherungsnummer\b", re.IGNORECASE), "ssn"),  # German
    (
        re.compile(r"\bnuméro\s+de\s+sécurité\s+sociale\b", re.IGNORECASE),
        "ssn",
    ),  # French
    # Data protection / GDPR terms
    (re.compile(r"\bdatenschutz\b", re.IGNORECASE), "data protection"),  # German
    (
        re.compile(r"\bprotection\s+des\s+données\b", re.IGNORECASE),
        "data protection",
    ),  # French
    (re.compile(r"\bdsgvo\b", re.IGNORECASE), "gdpr"),  # German GDPR
    (re.compile(r"\brgpd\b", re.IGNORECASE), "gdpr"),  # French GDPR
    # Security / Compliance
    (re.compile(r"\bsicherheit\b", re.IGNORECASE), "security"),  # German
    (re.compile(r"\bsécurité\b", re.IGNORECASE), "security"),  # French
    (re.compile(r"\bcompliance\b", re.IGNORECASE), "compliance"),
    (re.compile(r"\bkonformität\b", re.IGNORECASE), "compliance"),  # German
    (re.compile(r"\bconformité\b", re.IGNORECASE), "compliance"),  # French
    # Risk
    (re.compile(r"\brisikobewertung\b", re.IGNORECASE), "risk assessment"),  # German
    (
        re.compile(r"\bévaluation\s+des\s+risques\b", re.IGNORECASE),
        "risk assessment",
    ),  # French
    # Audit
    (re.compile(r"\bprüfung\b", re.IGNORECASE), "audit"),  # German
    (re.compile(r"\baudit\b", re.IGNORECASE), "audit"),
    # Access control
    (re.compile(r"\bzugriffskontrolle\b", re.IGNORECASE), "access control"),  # German
    (re.compile(r"\bcontrôle\s+d'accès\b", re.IGNORECASE), "access control"),  # French
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
    text = text.translate(_UNICODE_PUNCT_TRANSLATION)
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


def compare_clause_meaning(
    left: ClauseMeaning, right: ClauseMeaning, language: str = ""
) -> MeaningComparison:
    subject_score = token_overlap(left.subject, right.subject, language)
    action_score = (
        1.0
        if left.action and left.action == right.action
        else token_overlap(left.action, right.action, language)
    )
    object_score = token_overlap(left.object, right.object, language)
    condition_score = token_overlap(left.condition, right.condition, language)

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


def token_overlap(left: str, right: str, language: str = "") -> float:
    left_tokens = set(tokenize_policy_text(left, language))
    right_tokens = set(tokenize_policy_text(right, language))
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return intersection / union if union else 0.0


def tokenize_policy_text(value: str, language: str = "") -> list[str]:
    stopwords = get_stopwords(language)
    tokens = [
        token
        for token in _TOKEN_RE.findall(clean_policy_text(value))
        if len(token) > 1 and token not in stopwords
    ]
    return tokens


def obligation_rank(obligation: str) -> int:
    return OBLIGATION_STRENGTH.get((obligation or "").lower(), -1)


def describe_obligation_change(left: str, right: str) -> str:
    left_lower = (left or "").lower()
    right_lower = (right or "").lower()
    left_rank = obligation_rank(left)
    right_rank = obligation_rank(right)

    # Check for negation inversion (e.g., "shall" <-> "shall_not")
    left_is_negation = left_lower.endswith("_not")
    right_is_negation = right_lower.endswith("_not")
    if left_is_negation != right_is_negation and left_rank >= 0 and right_rank >= 0:
        return "inverted"

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


def average_token_overlap(
    values: Iterable[str], other_values: Iterable[str], language: str = ""
) -> float:
    left = " ".join(value for value in values if value)
    right = " ".join(value for value in other_values if value)
    return token_overlap(left, right, language)


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
