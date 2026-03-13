# src/grc_policy_server/services/llm/ollama_client.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from grc_policy_server.core.config import settings as app_settings
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

        batch_size = app_settings.semantic_extraction_batch_size
        for start in range(0, len(uncached), batch_size):
            batch = uncached[start : start + batch_size]
            batch_texts = [text for _, text in batch]
            extracted = await self._extract_policy_meanings_batch(batch_texts)
            for (index, text), meaning in zip(batch, extracted, strict=False):
                payload = self._normalize_meaning_dict(meaning)
                self._meaning_cache[text] = payload
                results[index] = dict(payload)

        return [
            result or self._meaning_dict(extract_clause_meaning(text))
            for result, text in zip(results, normalized_texts, strict=False)
        ]

    async def summarize_diff(
        self,
        *,
        old_text: str,
        new_text: str,
        section: str,
    ) -> str:
        prompt = self._prompt_summarize_diff(
            old_text=old_text, new_text=new_text, section=section
        )
        return await self._generate_text(prompt)

    async def summarize_changes(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
    ) -> str:
        compact = self._compact_diffs_for_summary(key_differences)
        prompt = self._prompt_summarize_changes(
            doc1_name=doc1_name, doc2_name=doc2_name, diffs=compact
        )
        return await self._generate_text(prompt)

    async def generate_followups(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
        max_questions: int = 4,
    ) -> List[str]:
        compact = self._compact_diffs_for_followups(key_differences)
        prompt = self._prompt_followups(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            diffs=compact,
            max_questions=max_questions,
        )
        text = await self._generate_text(prompt)
        return self._parse_numbered_questions(text, max_questions=max_questions)

    def close(self) -> None:
        return None

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
        self, *, old_text: str, new_text: str, section: str
    ) -> str:
        return f"""
You are a GRC compliance analyst.

Task: Summarize the change in one paragraph, strictly grounded in the provided texts.

Rules:
- Use ONLY the OLD and NEW text below.
- Do NOT invent new requirements, dates, or sections.
- If change is ambiguous, say so.

Section: {section}

OLD:
{old_text}

NEW:
{new_text}

Write a concise change summary and (if clear) the likely compliance impact.
""".strip()

    def _prompt_summarize_changes(
        self, *, doc1_name: str, doc2_name: str, diffs: List[Dict[str, Any]]
    ) -> str:
        return f"""
You are a GRC compliance analyst creating an executive summary of document changes.

Documents being compared:
- Document A (baseline): {doc1_name}
- Document B (updated): {doc2_name}

The documents may be in any language. Provide the summary in English.

Write a 3-6 sentence executive summary following this structure:
1. Overall scope: How many changes and which areas are affected
2. Critical changes: Highlight any obligation changes (shall/must strengthened, weakened, or inverted to prohibitions)
3. Compliance impact: What actions may be required due to these changes

Priority guidance:
- Obligation inversions (e.g., "shall" changed to "shall not") are CRITICAL
- Obligation strengthening (e.g., "should" to "must") is HIGH priority
- Obligation weakening (e.g., "must" to "may") is HIGH priority
- New requirements (ADDED) need attention
- Removed requirements may indicate relaxed controls

Rules:
- Use ONLY information from the provided differences JSON
- Do NOT invent changes, sections, or impacts not present in the data
- Use clear, auditor-friendly language suitable for compliance reporting

Differences JSON:
{json.dumps(diffs, ensure_ascii=False)}

Executive summary:
""".strip()

    def _prompt_followups(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        diffs: List[Dict[str, Any]],
        max_questions: int,
    ) -> str:
        return f"""
You are a GRC auditor reviewing policy changes between two document versions.

Documents:
- Baseline: {doc1_name}
- Updated: {doc2_name}

Generate up to {max_questions} follow-up questions that a compliance team should investigate.

Focus on questions that address:
- Implementation gaps: What processes need updating to meet new requirements?
- Risk assessment: Are there new risks from weakened or removed controls?
- Timeline concerns: Are there deadlines or effective dates to consider?
- Evidence requirements: What documentation is needed to demonstrate compliance?
- Stakeholder impact: Who needs to be informed of these changes?

Example good questions:
- "What is the implementation timeline for the new MFA requirement in Section 3.2?"
- "Have existing controls been assessed against the strengthened data retention requirements?"
- "Which teams are responsible for updating procedures to reflect the removed exception in Section 5.1?"

Rules:
- Base questions ONLY on the provided differences - do not invent sections or changes
- Make questions specific and actionable
- Prioritize questions about Critical and High impact changes

Differences JSON:
{json.dumps(diffs, ensure_ascii=False)}

Return as a numbered list (1., 2., 3., ...):
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
    ) -> List[Dict[str, str]]:
        if not texts:
            return []

        prompt = self._prompt_extract_policy_meanings(texts)

        try:
            response_text = await self._generate_text(prompt)
            payload = self._parse_meaning_payload(response_text, expected=len(texts))
            if payload is not None:
                return payload
        except Exception:
            logger.exception("failed to extract policy meanings with ollama")

        return [self._meaning_dict(extract_clause_meaning(text)) for text in texts]

    def _prompt_extract_policy_meanings(self, texts: List[str]) -> str:
        items = [{"index": index, "text": text} for index, text in enumerate(texts)]
        return f"""
You extract structured policy meaning for auditor-grade document comparison.

The input policy text may be in any language. Understand the original language, but normalize the output fields into concise English.

Return STRICT JSON only as an array with exactly {len(texts)} objects in the same order as the input.

Field definitions:
- obligation: The strength of requirement. Use one of: "", "may", "should", "recommended", "required", "must", "shall", "shall_not", "must_not"
- subject: WHO must perform the action (e.g., "organization", "data controller", "employees")
- action: WHAT must be done, as a verb phrase (e.g., "implement", "review", "document")
- object: WHAT the action applies to (e.g., "access controls", "security policies", "personal data")
- condition: WHEN or IF - any conditions, triggers, or exceptions (e.g., "when processing sensitive data", "unless approved by management")

Examples:

Input: "Organizations shall implement multi-factor authentication for all privileged accounts."
Output: {{"obligation": "shall", "subject": "organizations", "action": "implement", "object": "multi-factor authentication for privileged accounts", "condition": ""}}

Input: "Personal data must not be retained beyond the specified retention period."
Output: {{"obligation": "must_not", "subject": "", "action": "retain", "object": "personal data", "condition": "beyond retention period"}}

Input: "Where technically feasible, encryption should be applied to data at rest."
Output: {{"obligation": "should", "subject": "", "action": "apply encryption", "object": "data at rest", "condition": "where technically feasible"}}

Input: "Die Organisation muss alle Sicherheitsvorfälle dokumentieren."
Output: {{"obligation": "must", "subject": "organization", "action": "document", "object": "security incidents", "condition": ""}}

Rules:
- For negations like "shall not" or "must not", use "shall_not" or "must_not" as the obligation.
- If no explicit obligation word exists, set obligation to "".
- Keep all field values concise (under 10 words each).
- Output ONLY the JSON array, no commentary or markdown.

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
        valid_obligations = {"", "may", "should", "recommended", "required", "must", "shall", "shall_not", "must_not"}
        if obligation not in valid_obligations:
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
        # Check negations first
        if "shall not" in lowered or "shall_not" in lowered:
            return "shall_not"
        if "must not" in lowered or "must_not" in lowered:
            return "must_not"
        # Then positive obligations
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
