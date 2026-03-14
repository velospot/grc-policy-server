# src/grc_policy_server/services/llm/ollama_client.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from grc_policy_server.core.logging import logging
from grc_policy_server.models.schemas import KeyDifference
from grc_policy_server.services.llm.base import BaseLLM
from grc_policy_server.services.comparision.policy_semantics import (
    ClauseMeaning,
    extract_clause_meaning,
)
from grc_policy_server.utils.hashing import normalize_whitespace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OllamaSettings:
    base_url: str = "http://localhost:11434"
    chat_model: str = "granite3.3:8b"
    embed_model: str = "qwen3-embedding"
    timeout_sec: float = 180.0


class OllamaClient(BaseLLM):
    """
    Ollama-only implementation for:
      - embeddings (api/embeddings)
      - generation (api/generate)

    Notes:
      - We keep prompts compact and structured to reduce hallucination risk.
      - We do NOT send full sourceText/citations into LLM for summaries by default.
    """

    def __init__(self, settings: Optional[OllamaSettings] = None):
        self.settings = settings or OllamaSettings()
        self._meaning_cache: dict[str, dict[str, str]] = {}

    def embed(self, text: str) -> list[float]:
        payload = {
            "model": self.settings.embed_model,
            "input": text,
        }
        response = self._post_json_sync("/api/embed", payload)
        embeddings = response.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            first = embeddings[0]
            if isinstance(first, list):
                return [float(value) for value in first]

        embedding = response.get("embedding")
        if isinstance(embedding, list):
            return [float(value) for value in embedding]

        raise RuntimeError(f"Unexpected Ollama embedding response: {response}")

    async def extract_policy_meanings(
        self,
        *,
        texts: List[str],
        language: str = "",
    ) -> List[Dict[str, str]]:
        normalized_texts = [normalize_whitespace(text or "") for text in texts]
        results: list[dict[str, str] | None] = [None] * len(normalized_texts)
        uncached: list[tuple[int, str]] = []

        for index, text in enumerate(normalized_texts):
            if not text:
                results[index] = self._meaning_dict(ClauseMeaning("", "", "", "", ""))
                continue
            cached = self._meaning_cache.get(text)
            if cached is not None:
                results[index] = dict(cached)
                continue
            uncached.append((index, text))

        for start in range(0, len(uncached), 8):
            batch = uncached[start : start + 8]
            batch_texts = [text for _, text in batch]
            extracted = await self._extract_policy_meanings_batch(batch_texts, language=language)
            for (index, text), meaning in zip(batch, extracted, strict=False):
                payload = self._normalize_meaning_dict(meaning)
                self._meaning_cache[text] = payload
                results[index] = dict(payload)

        return [
            result or self._meaning_dict(extract_clause_meaning(text))
            for result, text in zip(results, normalized_texts, strict=False)
        ]

    async def detect_language(self, text_sample: str) -> str:
        """
        Detect the language of a document from a text sample.
        Returns language code: 'en', 'de', 'fr', or 'unknown'.
        """
        if not text_sample or not text_sample.strip():
            return "unknown"

        # Take first 500 characters to keep it efficient
        sample = text_sample[:500].strip()
        if not sample:
            return "unknown"

        prompt = f"""
Identify the primary language of this text. Reply with ONLY one of these codes:
- en (English)
- de (German)
- fr (French)
- unknown (if unclear or other language)

Text:
{sample}

Language code:"""

        try:
            response = await self._generate_text(prompt)
            code = response.strip().lower()
            # Check for German indicators
            if code.startswith("de") or "german" in code or "deutsch" in code:
                return "de"
            # Check for French indicators
            if code.startswith("fr") or "french" in code or "francais" in code or "français" in code:
                return "fr"
            # Check for English indicators
            if code.startswith("en") or "english" in code:
                return "en"
            # Fallback: check if response contains just the code
            first_word = code.split()[0] if code.split() else ""
            if first_word in {"de", "german", "deutsch"}:
                return "de"
            if first_word in {"fr", "french", "francais", "français"}:
                return "fr"
            if first_word in {"en", "english"}:
                return "en"
            return "unknown"
        except Exception:
            logger.exception("failed to detect language")
            return "unknown"

    async def summarize_diff(
        self,
        *,
        old_text: str,
        new_text: str,
        section: str,
        language: str = "",
    ) -> str:
        prompt = self._prompt_summarize_diff(
            old_text=old_text, new_text=new_text, section=section, language=language
        )
        return await self._generate_text(prompt)

    async def summarize_changes(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
        language: str = "",
    ) -> str:
        compact = self._compact_diffs_for_summary(key_differences)
        prompt = self._prompt_summarize_changes(
            doc1_name=doc1_name, doc2_name=doc2_name, diffs=compact, language=language
        )
        return await self._generate_text(prompt)

    async def generate_followups(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
        max_questions: int = 4,
        language: str = "",
    ) -> List[str]:
        compact = self._compact_diffs_for_followups(key_differences)
        prompt = self._prompt_followups(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            diffs=compact,
            max_questions=max_questions,
            language=language,
        )
        text = await self._generate_text(prompt)
        return self._parse_numbered_questions(text, max_questions=max_questions)

    def close(self) -> None:
        return None

    def _language_hint(self, language: str) -> str:
        """Generate a language hint for prompts based on detected language code."""
        lang_map = {
            "en": "The input text is in English. ",
            "de": "The input text is in German (Deutsch). ",
            "fr": "The input text is in French (Français). ",
        }
        return lang_map.get(language, "The input text may be in English, German, or French. ")

    # -------------------------
    # HTTP helpers
    # -------------------------

    def _post_json_sync(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.settings.base_url}{path}"
        with httpx.Client(timeout=self.settings_timeout) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"Ollama response is not JSON object: {data}")
            return data

    async def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.settings.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.settings_timeout) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"Ollama response is not JSON object: {data}")
            return data

    @property
    def settings_timeout(self) -> float:
        return self.settings.timeout_sec

    # -------------------------
    # Prompt builders
    # -------------------------

    def _prompt_summarize_diff(
        self, *, old_text: str, new_text: str, section: str, language: str = ""
    ) -> str:
        lang_hint = self._language_hint(language)
        return f"""
You are a GRC compliance analyst.

{lang_hint}Understand the original language but write the summary in English.

Task: Summarize the change in one sentence, strictly grounded in the provided texts.

Rules:
- Use ONLY the OLD and NEW text below.
- Do NOT invent new requirements, dates, or sections.
- If change is ambiguous, say so.
- Be concise: one sentence describing what changed and its impact.

Section: {section}

OLD:
{old_text}

NEW:
{new_text}

One-line summary:
""".strip()

    def _prompt_summarize_changes(
        self, *, doc1_name: str, doc2_name: str, diffs: List[Dict[str, Any]], language: str = ""
    ) -> str:
        lang_hint = self._language_hint(language)
        return f"""
You are a GRC compliance analyst. Summarize changes between two document versions.

{lang_hint}Understand the original language but write the summary in English.

Document A: {doc1_name}
Document B: {doc2_name}

You MUST follow these rules:
- Use ONLY the provided differences JSON.
- Do NOT invent changes, sections, or impacts not present.

Output format (follow exactly):
1. First, write an executive summary describing the overall nature and impact of changes. Length should be proportional to the significance and volume of changes.
2. Then write "Key Changes:" on a new line.
3. Then list each change as a bullet point with format: "- Page X, Section Y: [brief description]"
   (If page is unavailable, use "- Section Y: [description]")

Differences JSON:
{json.dumps(diffs, ensure_ascii=False)}

Summary:
""".strip()

    def _prompt_followups(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        diffs: List[Dict[str, Any]],
        max_questions: int,
        language: str = "",
    ) -> str:
        lang_hint = self._language_hint(language)
        return f"""
You are a GRC auditor.

{lang_hint}Write questions in English.

Generate up to {max_questions} follow-up questions based ONLY on the diff list.
Do NOT invent sections not present.

Document A: {doc1_name}
Document B: {doc2_name}

Diffs JSON:
{json.dumps(diffs, ensure_ascii=False)}

Return as a numbered list (1., 2., 3., ...). Keep questions specific and actionable.
""".strip()

    # -------------------------
    # Diff compaction (token safety)
    # -------------------------

    def _compact_diffs_for_summary(
        self, diffs: List[KeyDifference]
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for d in diffs[:50]:
            out.append(
                {
                    "changeType": getattr(d, "changeType", None),
                    "section": d.section,
                    "page": getattr(d, "page", None) or getattr(d, "pageNumber", None),
                    "impact": d.impact,
                    "doc1Content": d.doc1Content,
                    "doc2Content": d.doc2Content,
                }
            )
        return out

    def _compact_diffs_for_followups(
        self, diffs: List[KeyDifference]
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for d in diffs[:60]:
            out.append(
                {
                    "changeType": getattr(d, "changeType", None),
                    "section": d.section,
                    "impact": d.impact,
                }
            )
        return out

    # -------------------------
    # Output parsing
    # -------------------------

    def _parse_numbered_questions(self, text: str, *, max_questions: int) -> List[str]:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        qs: List[str] = []
        for ln in lines:
            # strip common numbering patterns: "1.", "1)", "-", "•"
            cleaned = (
                ln.lstrip("0123456789")
                .lstrip(".")
                .lstrip(")")
                .lstrip("-")
                .lstrip("•")
                .strip()
            )
            if cleaned:
                qs.append(cleaned)
            if len(qs) >= max_questions:
                break
        # fallback if model returns a blob paragraph
        if not qs and text.strip():
            qs = [text.strip()[:300]]
        return qs

    async def _extract_policy_meanings_batch(
        self,
        texts: List[str],
        language: str = "",
    ) -> List[Dict[str, str]]:
        if not texts:
            return []

        prompt = self._prompt_extract_policy_meanings(texts, language=language)

        try:
            response_text = await self._generate_text(prompt)
            payload = self._parse_meaning_payload(response_text, expected=len(texts))
            if payload is not None:
                return payload
        except Exception:
            logger.exception("failed to extract policy meanings with ollama")

        return [self._meaning_dict(extract_clause_meaning(text)) for text in texts]

    def _prompt_extract_policy_meanings(self, texts: List[str], language: str = "") -> str:
        items = [{"index": index, "text": text} for index, text in enumerate(texts)]
        lang_hint = self._language_hint(language)
        return f"""
You extract a structured semantic fingerprint from document text to enable precise difference detection.

{lang_hint}Understand the original language, but normalize the output fields into concise English.

Return STRICT JSON only as an array with exactly {len(texts)} objects in the same order as the input.
Each object must have these string fields:
- obligation: one of "", "may", "should", "recommended", "required", "must", "shall", "must_not", "shall_not"
- subject
- action
- object
- condition

Field definitions:
- obligation: The modality or strength of the statement (see values below)
- subject: The actor, entity, or topic of the statement (who/what is responsible, discussed, or referenced)
- action: The verb, operation, or relationship described (what is done, stated, defined, or asserted)
- object: The target, value, or content the action applies to (include specific numbers, dates, names, quantities, or key terms)
- condition: Any qualifiers, constraints, scope, context, exceptions, timeframes, or prerequisites

Obligation/modality values (weakest to strongest):
- "": Factual, descriptive, or definitional statement
- "may": Permission, possibility, or optional
- "should": Advisory, suggested, or expected
- "recommended": Explicitly recommended
- "required": Mandatory or necessary
- "must": Strong requirement
- "shall": Formal or contractual obligation
- "must_not"/"shall_not": Prohibition or forbidden

Extraction goals - capture differences of ANY type:
- Factual: Numbers, dates, names, quantities, versions go in object field
- Semantic: Core meaning via subject + action + object combination
- Contextual: Scope, exceptions, timeframes, prerequisites go in condition field
- Modality: Strength or nature of statement in obligation field

Rules:
- Extract the complete semantic fingerprint so any change in meaning is detectable.
- Preserve specific values (numbers, dates, names) exactly in object field.
- If no modality exists, set obligation to "".
- If subject is implicit, infer from context or use "entity".
- Keep fields concise but include all semantically significant content.
- Put all qualifiers, scope limits, and context in condition field.
- Ignore punctuation, formatting, and stylistic variations - focus only on meaning.
- Empty string for any field that cannot be determined.
- Do not add commentary, markdown, or extra keys.

Input JSON:
{json.dumps(items, ensure_ascii=False)}
""".strip()

    def _parse_meaning_payload(
        self,
        payload_text: str,
        *,
        expected: int,
    ) -> List[Dict[str, str]] | None:
        payload = payload_text.strip()
        parsed = None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            start = payload.find("[")
            end = payload.rfind("]")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(payload[start : end + 1])
                except json.JSONDecodeError:
                    return None
        if not isinstance(parsed, list) or len(parsed) != expected:
            return None
        return [self._normalize_meaning_dict(item) for item in parsed]

    def _normalize_meaning_dict(self, value: Any) -> Dict[str, str]:
        if not isinstance(value, dict):
            return self._meaning_dict(ClauseMeaning("", "", "", "", ""))

        obligation = str(value.get("obligation") or "").strip().lower()
        if obligation not in {"", "may", "should", "recommended", "required", "must", "shall", "must_not", "shall_not"}:
            obligation = self._map_obligation_value(obligation)

        return {
            "obligation": obligation,
            "subject": normalize_whitespace(str(value.get("subject") or "")).lower(),
            "action": normalize_whitespace(str(value.get("action") or "")).lower(),
            "object": normalize_whitespace(str(value.get("object") or "")).lower(),
            "condition": normalize_whitespace(str(value.get("condition") or "")).lower(),
        }

    def _map_obligation_value(self, value: str) -> str:
        lowered = value.lower()
        # Check for negations/prohibitions first
        if "shall" in lowered and ("not" in lowered or "never" in lowered or "prohibit" in lowered or "forbid" in lowered):
            return "shall_not"
        if "must" in lowered and ("not" in lowered or "never" in lowered or "prohibit" in lowered or "forbid" in lowered):
            return "must_not"
        if "prohibit" in lowered or "forbid" in lowered or "forbidden" in lowered:
            return "must_not"
        # Then check positive obligations
        if "shall" in lowered:
            return "shall"
        if "must" in lowered:
            return "must"
        if "require" in lowered or "mandatory" in lowered or "obligat" in lowered:
            return "required"
        if "recommend" in lowered:
            return "recommended"
        if "should" in lowered:
            return "should"
        if "may" in lowered or "optional" in lowered:
            return "may"
        return ""

    def _meaning_dict(self, meaning: ClauseMeaning) -> Dict[str, str]:
        return {
            "obligation": meaning.obligation,
            "subject": meaning.subject,
            "action": meaning.action,
            "object": meaning.object,
            "condition": meaning.condition,
        }

    async def _generate_text(self, prompt: str) -> str:
        response = await self._post_json(
            "/api/generate",
            {
                "model": self.settings.chat_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0},
            },
        )
        return str(response.get("response") or "").strip()
