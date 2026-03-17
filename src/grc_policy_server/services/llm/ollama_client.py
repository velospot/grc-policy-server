# src/grc_policy_server/services/llm/ollama_client.py
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from grc_policy_server.core.logging import logging
from grc_policy_server.models.schemas import KeyDifference
from grc_policy_server.services.comparision.policy_semantics import (
    ClauseMeaning,
    extract_clause_meaning,
)
from grc_policy_server.services.llm.base import BaseLLM
from grc_policy_server.utils.hashing import normalize_whitespace

logger = logging.getLogger(__name__)

PROMPT_EN = """
You are a compliance analyst. Summarize changes between two document versions.

Document A: {doc1_name}
Document B: {doc2_name}

Rules:
- Use ONLY the Differences JSON.
- Do NOT invent or assume anything not explicitly stated.
- Ignore duplicates and low-impact changes.
- Output 3–6 items only.
- Write in English.
- No introduction or conclusion.

Be SPECIFIC about what changed:
- Quote exact values when possible (old value → new value)
- Name specific rows, columns, or cells that changed
- Describe additions/removals precisely

For each item, use this EXACT Markdown format:

- **[ADDED|REMOVED|MODIFIED] <Section or topic>**
  - Change: <one concise sentence describing the change>
  - Implication: <1–2 concise points explaining what this means in practice, based only on the JSON>

Constraints:
- One meaningful change per item.
- Do not merge unrelated changes.
- If implications are not explicit, infer only direct operational impact (e.g., scope, timing, obligation).
- No numbering.
- Do NOT use vague descriptions like "table updated" or "content modified".

Differences JSON:
{diffs}

Summary:
""".strip()

PROMPT_DE = """
Sie sind Compliance-Analyst. Fassen Sie die Änderungen zwischen zwei Dokumentversionen zusammen.

Dokument A: {doc1_name}
Dokument B: {doc2_name}

Regeln:
- Verwenden Sie AUSSCHLIESSLICH das Differences-JSON.
- Nichts erfinden oder annehmen, was nicht ausdrücklich enthalten ist.
- Duplikate und geringfügige Änderungen ignorieren.
- Genau 3–6 Punkte ausgeben.
- Schreiben Sie auf Deutsch.
- Keine Einleitung und kein Schluss.

Sei SPEZIFISCH, was sich geändert hat:

- Nenne nach Möglichkeit exakte Werte (alter Wert → neuer Wert)
- Benenne die konkreten Zeilen, Spalten oder Zellen, die sich geändert haben
- Beschreibe Ergänzungen/Entfernungen präzise

Für jeden Punkt verwenden Sie GENAU dieses Markdown-Format:

- **[ADDED|REMOVED|MODIFIED] <Abschnitt oder Thema>**
  - Änderung: <ein kurzer Satz zur Beschreibung der Änderung>
  - Auswirkung: <1–2 kurze Punkte, was dies praktisch bedeutet, nur basierend auf dem JSON>

Vorgaben:
- Pro Punkt nur eine wesentliche Änderung.
- Keine Zusammenlegung nicht zusammengehöriger Änderungen.
- Falls Auswirkungen nicht ausdrücklich genannt sind, nur direkte operative Folgen ableiten (z. B. Umfang, Fristen, Verpflichtungen).
- Keine Nummerierung.
- Verwende KEINE vagen Beschreibungen wie „Tabelle aktualisiert“ oder „Inhalt geändert“.

Differences JSON:
{diffs}

Zusammenfassung:
""".strip()

PROMPT_FR = """
Vous êtes analyste conformité. Résumez les modifications entre deux versions d’un document.

Document A : {doc1_name}
Document B : {doc2_name}

Règles :
- Utiliser UNIQUEMENT le JSON des différences.
- Ne rien inventer ni supposer au-delà des informations fournies.
- Ignorer les doublons et changements mineurs.
- Produire exactement 3 à 6 éléments.
- Écrire en français.
- Pas d’introduction ni de conclusion.

Soyez PRÉCIS sur ce qui a changé:
- Indiquez les valeurs exactes lorsque possible (ancienne valeur → nouvelle valeur)
- Nommez les lignes, colonnes ou cellules spécifiques qui ont été modifiées
- Décrivez précisément les ajouts/suppressions

Pour chaque élément, utiliser EXACTEMENT ce format Markdown :

- **[ADDED|REMOVED|MODIFIED] <Section ou sujet>**
  - Changement : <une phrase concise décrivant la modification>
  - Implication : <1 à 2 points concis expliquant ce que cela implique en pratique, uniquement d’après le JSON>

Contraintes :
- Une seule modification significative par élément.
- Ne pas fusionner des modifications non liées.
- Si les implications ne sont pas explicites, déduire uniquement l’impact opérationnel direct (ex. portée, délais, obligations).
- Pas de numérotation.
- N’utilisez PAS de descriptions vagues telles que « tableau mis à jour » ou « contenu modifié »

Differences JSON :
{diffs}

Résumé :
""".strip()

PROMPTS = {
    "en": PROMPT_EN,
    "de": PROMPT_DE,
    "fr": PROMPT_FR,
}


@dataclass(frozen=True)
class OllamaSettings:
    base_url: str = "http://localhost:11434"
    chat_model: str = "granite3.3:8b"
    embed_model: str = "qwen3-embedding"
    connect_timeout_sec: float = 10.0
    read_timeout_sec: float = 300.0
    write_timeout_sec: float = 30.0
    max_retries: int = 2

    @property
    def timeout_sec(self) -> float:
        """Back-compat: largest of the configured timeouts."""
        return self.read_timeout_sec


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
        self._async_client = httpx.AsyncClient(
            base_url=self.settings.base_url,
            timeout=httpx.Timeout(
                connect=self.settings.connect_timeout_sec,
                read=self.settings.read_timeout_sec,
                write=self.settings.write_timeout_sec,
                pool=self.settings.connect_timeout_sec,
            ),
        )
        self._sync_client = httpx.Client(
            timeout=httpx.Timeout(
                connect=self.settings.connect_timeout_sec,
                read=self.settings.read_timeout_sec,
                write=self.settings.write_timeout_sec,
                pool=self.settings.connect_timeout_sec,
            ),
        )

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
        markdown_texts: List[str] | None = None,
        language: str = "",
    ) -> List[Dict[str, str]]:
        normalized_texts = [normalize_whitespace(text or "") for text in texts]
        # Use markdown_texts if provided, otherwise fall back to plain texts
        effective_markdown = markdown_texts if markdown_texts else texts
        results: list[dict[str, str] | None] = [None] * len(normalized_texts)
        uncached: list[tuple[int, str, str]] = []  # (index, text, markdown)

        for index, text in enumerate(normalized_texts):
            if not text:
                results[index] = self._meaning_dict(ClauseMeaning("", "", "", "", ""))
                continue
            cached = self._meaning_cache.get(text)
            if cached is not None:
                results[index] = dict(cached)
                continue
            markdown = (
                effective_markdown[index] if index < len(effective_markdown) else text
            )
            uncached.append((index, text, markdown))

        batches = [uncached[i : i + 8] for i in range(0, len(uncached), 8)]
        batch_results = await asyncio.gather(
            *[
                self._extract_policy_meanings_batch(
                    [text for _, text, _ in batch],
                    markdown_texts=[markdown for _, _, markdown in batch],
                    language=language,
                )
                for batch in batches
            ]
        )
        for batch, extracted in zip(batches, batch_results, strict=False):
            for (index, text, _), meaning in zip(batch, extracted, strict=False):
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

        sample = text_sample[:1000].lower()
        tokens = re.findall(r"[a-zA-ZÀ-ÿ]+", sample)
        if not tokens:
            return "unknown"

        lexicons = {
            "en": {
                "the",
                "and",
                "shall",
                "must",
                "should",
                "policy",
                "document",
                "requirements",
                "control",
                "controls",
                "access",
                "security",
                "is",
                "are",
            },
            "de": {
                "der",
                "die",
                "das",
                "und",
                "nicht",
                "mit",
                "sind",
                "muss",
                "müssen",
                "soll",
                "sollen",
                "richtlinie",
                "dokument",
                "anforderungen",
                "zugriff",
                "sicherheit",
            },
            "fr": {
                "le",
                "la",
                "les",
                "et",
                "pas",
                "avec",
                "sont",
                "doit",
                "doivent",
                "politique",
                "document",
                "exigences",
                "accès",
                "securite",
                "sécurité",
                "conformité",
            },
        }
        scores = {code: 0 for code in lexicons}
        for token in tokens:
            for code, lexicon in lexicons.items():
                if token in lexicon:
                    scores[code] += 1

        if any(ch in sample for ch in "äöüß"):
            scores["de"] += 2
        if any(ch in sample for ch in "àâçéèêëîïôûùüÿœæ"):
            scores["fr"] += 2

        best = max(scores, key=scores.get)
        best_score = scores[best]
        if best_score == 0:
            return "unknown"
        winners = [code for code, score in scores.items() if score == best_score]
        if len(winners) != 1:
            return "unknown"
        return best

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
        return await self._generate_text(prompt, temperature=0.3)

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
        return await self._generate_text(prompt, temperature=0.5)

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
        text = await self._generate_text(prompt, temperature=0.5)
        return self._parse_numbered_questions(text, max_questions=max_questions)

    def close(self) -> None:
        """Sync best-effort close. Use aclose() in async contexts."""
        self._sync_client.close()
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_client.aclose())
        except RuntimeError:
            asyncio.run(self._async_client.aclose())

    async def aclose(self) -> None:
        self._sync_client.close()
        await self._async_client.aclose()

    def _language_hint(self, language: str) -> str:
        """Generate a language hint for prompts based on detected language code."""
        lang_map = {
            "en": "The input text is in English. ",
            "de": "The input text is in German (Deutsch). ",
            "fr": "The input text is in French (Français). ",
        }
        return lang_map.get(
            language, "The input text may be in English, German, or French. "
        )

    # -------------------------
    # HTTP helpers
    # -------------------------

    def _post_json_sync(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.settings.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = self._sync_client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise RuntimeError(f"Ollama response is not JSON object: {data}")
                return data
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning(
                    "Ollama sync timeout on attempt %d/%d: %s",
                    attempt + 1,
                    self.settings.max_retries + 1,
                    path,
                )
        raise RuntimeError(
            f"Ollama request timed out after {self.settings.max_retries + 1} attempts"
        ) from last_exc

    async def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                r = await self._async_client.post(path, json=payload)
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict):
                    raise RuntimeError(f"Ollama response is not JSON object: {data}")
                return data
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning(
                    "Ollama async timeout on attempt %d/%d: %s",
                    attempt + 1,
                    self.settings.max_retries + 1,
                    path,
                )
        raise RuntimeError(
            f"Ollama request timed out after {self.settings.max_retries + 1} attempts"
        ) from last_exc

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
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        diffs: List[Dict[str, Any]],
        language: str = "",
    ) -> str:
        lang_code = language if language in PROMPTS else "en"
        canonical_diffs = json.dumps(
            diffs,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return PROMPTS[lang_code].format(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            diffs=canonical_diffs,
        )

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
        by_section: dict[str, dict[str, list[str]]] = {}
        for d in diffs[:50]:
            key = str(d.section or "Unknown Section")
            section_entry = by_section.setdefault(key, {"doc1": [], "doc2": []})
            if d.doc1Content:
                section_entry["doc1"].append(d.doc1Content)
            if d.doc2Content:
                section_entry["doc2"].append(d.doc2Content)

        out: List[Dict[str, Any]] = []
        for d in diffs[:50]:
            key = str(d.section or "Unknown Section")
            section_entry = by_section.get(key, {"doc1": [], "doc2": []})
            out.append(
                {
                    "changeType": getattr(d, "changeType", None),
                    "section": key,
                    "page": getattr(d, "page", None) or getattr(d, "pageNumber", None),
                    "doc1Content": d.doc1Content,
                    "doc2Content": d.doc2Content,
                    "doc1SectionContent": " ".join(section_entry["doc1"]).strip(),
                    "doc2SectionContent": " ".join(section_entry["doc2"]).strip(),
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
        markdown_texts: List[str] | None = None,
        language: str = "",
    ) -> List[Dict[str, str]]:
        if not texts:
            return []

        prompt = self._prompt_extract_policy_meanings(
            texts, markdown_texts=markdown_texts, language=language
        )

        try:
            response_text = await self._generate_text(prompt)
            payload = self._parse_meaning_payload(response_text, expected=len(texts))
            if payload is not None:
                return payload
        except Exception:
            logger.exception("failed to extract policy meanings with ollama")

        return [self._meaning_dict(extract_clause_meaning(text)) for text in texts]

    def _prompt_extract_policy_meanings(
        self,
        texts: List[str],
        markdown_texts: List[str] | None = None,
        language: str = "",
    ) -> str:
        # Build items with markdown for better LLM comprehension
        items = []
        for index, text in enumerate(texts):
            item: dict[str, Any] = {"index": index, "text": text}
            # Include markdown if available and different from plain text
            if markdown_texts and index < len(markdown_texts):
                md = markdown_texts[index]
                if md and md != text:
                    item["markdown"] = md
            items.append(item)

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
- If "markdown" field is present, use it to understand the document structure (headers, lists, emphasis) but extract meaning from the "text" field.
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
        if obligation not in {
            "",
            "may",
            "should",
            "recommended",
            "required",
            "must",
            "shall",
            "must_not",
            "shall_not",
        }:
            obligation = self._map_obligation_value(obligation)

        return {
            "obligation": obligation,
            "subject": normalize_whitespace(str(value.get("subject") or "")).lower(),
            "action": normalize_whitespace(str(value.get("action") or "")).lower(),
            "object": normalize_whitespace(str(value.get("object") or "")).lower(),
            "condition": normalize_whitespace(
                str(value.get("condition") or "")
            ).lower(),
        }

    def _map_obligation_value(self, value: str) -> str:
        lowered = value.lower()
        # Check for negations/prohibitions first
        if "shall" in lowered and (
            "not" in lowered
            or "never" in lowered
            or "prohibit" in lowered
            or "forbid" in lowered
        ):
            return "shall_not"
        if "must" in lowered and (
            "not" in lowered
            or "never" in lowered
            or "prohibit" in lowered
            or "forbid" in lowered
        ):
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

    async def _generate_text(self, prompt: str, temperature=None) -> str:
        temp = 0
        if temperature is not None:
            temp = temperature
        response = await self._post_json(
            "/api/generate",
            {
                "model": self.settings.chat_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temp},
            },
        )
        text = str(response.get("response") or "").strip()
        # Strip chain-of-thought tags emitted by granite/deepseek style models.
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return text
