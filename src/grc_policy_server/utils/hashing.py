from __future__ import annotations

import hashlib
import re
from uuid import NAMESPACE_URL, uuid5


_WHITESPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", (value or "").strip()).lower()


def slugify_text(value: str) -> str:
    normalized = _NON_WORD_RE.sub("-", normalize_text(value))
    return normalized.strip("-")


def stable_uuid(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, value))
