# src/grc_policy_server/services/llm/ollama_client.py
from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from grc_policy_server.core.logging import logging
from grc_policy_server.models.schemas import KeyDifference
from grc_policy_server.services.comparison.policy_semantics import (
    ClauseMeaning,
    extract_clause_meaning,
)
from grc_policy_server.services.llm.base import BaseLLM
from grc_policy_server.services.llm.testing_department import (
    get_testing_department_profile,
    normalize_testing_department,
)
from grc_policy_server.services.observability import tracing
from grc_policy_server.utils.hashing import normalize_whitespace

logger = logging.getLogger(__name__)

PROMPT_EN = """
You are an expert EMC compliance auditor specializing in IEC/CISPR standards, automotive EMC (CISPR 25, ISO 11452, TL 81000), and marine EMC (DNV CG-0339). Summarize changes between two document versions.

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
- Flag changes to test levels (V/m, dBµV, dBµA, dBµV/m) with numeric old → new values
- Flag changes to frequency ranges (kHz–GHz) with exact range boundaries
- Flag IEC/CISPR/ISO standard reference changes, including edition/year updates
- Flag changes to acceptance criteria (Class A/B/C/D/E, Performance Criterion A/B/C)
- Flag changes to test procedures, dwell times, or test setup parameters

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
Sie sind ein erfahrener EMV-Compliance-Auditor, spezialisiert auf IEC/CISPR-Normen, automotive EMV (CISPR 25, ISO 11452, TL 81000) und marine EMV (DNV CG-0339). Fassen Sie die Änderungen zwischen zwei Dokumentversionen zusammen.

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
Vous êtes un expert auditeur de conformité CEM spécialisé dans les normes IEC/CISPR, la CEM automobile (CISPR 25, ISO 11452, TL 81000) et la CEM marine (DNV CG-0339). Résumez les modifications entre deux versions d’un document.

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

PROMPT_SUMMARIZE_DIFF_EN = """


Task: Summarize the change in one sentence, strictly grounded in the provided texts.

Rules:
- Use ONLY the OLD and NEW text below.
- Do NOT invent new requirements, dates, or sections.
- If change is ambiguous, say so.
- Be concise: one sentence describing what changed and its impact.
- Write in English.

Section: {section}

OLD:
{old_text}

NEW:
{new_text}

One-line summary:
""".strip()

PROMPT_SUMMARIZE_DIFF_DE = """


Aufgabe: Fassen Sie die Änderung in genau einem Satz zusammen, ausschließlich basierend auf den bereitgestellten Texten.

Regeln:
- Verwenden Sie NUR den OLD- und NEW-Text.
- Erfinden Sie keine Anforderungen, Daten oder Abschnitte.
- Wenn die Änderung unklar ist, nennen Sie dies.
- Schreiben Sie präzise in einem Satz inklusive Auswirkung.
- Schreiben Sie auf Deutsch.

Abschnitt: {section}

ALT:
{old_text}

NEU:
{new_text}

Ein-Satz-Zusammenfassung:
""".strip()

PROMPT_SUMMARIZE_DIFF_FR = """
Vous êtes analyste conformité GRC.

Tâche : Résumer la modification en une seule phrase, uniquement à partir des textes fournis.

Règles :
- Utiliser UNIQUEMENT les textes OLD et NEW.
- Ne pas inventer d’exigences, de dates ou de sections.
- Si la modification est ambiguë, le préciser.
- Rester concis : une phrase avec le changement et son impact.
- Écrire en français.

Section : {section}

ANCIEN :
{old_text}

NOUVEAU :
{new_text}

Résumé en une phrase :
""".strip()

SUMMARIZE_DIFF_PROMPTS = {
    "en": PROMPT_SUMMARIZE_DIFF_EN,
    "de": PROMPT_SUMMARIZE_DIFF_DE,
    "fr": PROMPT_SUMMARIZE_DIFF_FR,
}

PROMPT_SUMMARIZE_EXPLANATIONS_EN = """

Task:
1) Use the structured Change Record JSON entries as the only source of truth.
2) Aggregate change explanations into concise points grouped by ADDED, MODIFIED, REMOVED.
3) Omit groups with no entries.

Output format (Markdown):
- **ADDED**
  - <point>
- **MODIFIED**
  - <point>
- **REMOVED**
  - <point>

Rules:
- Keep it brief and specific.
- Mention sections when available.
- Include table/formula/equation implications only if present in explanations.
- Do not invent facts beyond the JSON.
- Write in English.

Document A: {doc1_name}
Document B: {doc2_name}

Change Record JSON:
{explanations}
""".strip()

PROMPT_SUMMARIZE_EXPLANATIONS_DE = """

Aufgabe:
1) Verwenden Sie das strukturierte Change-Record-JSON als einzige Quelle.
2) Aggregieren Sie die Erklärungen in prägnante Punkte nach ADDED, MODIFIED, REMOVED.
3) Lassen Sie Gruppen ohne Einträge weg.

Ausgabeformat (Markdown):
- **ADDED**
  - <Punkt>
- **MODIFIED**
  - <Punkt>
- **REMOVED**
  - <Punkt>

Regeln:
- Kurz und spezifisch formulieren.
- Abschnitte nennen, wenn vorhanden.
- Auswirkungen zu Tabellen/Formeln/Gleichungen nur nennen, wenn im JSON vorhanden.
- Keine Fakten außerhalb des JSON erfinden.
- Schreiben Sie auf Deutsch.

Dokument A: {doc1_name}
Dokument B: {doc2_name}

Change-Record-JSON:
{explanations}
""".strip()

PROMPT_SUMMARIZE_EXPLANATIONS_FR = """
Vous êtes analyste conformité GRC.

Tâche :
1) Utiliser le JSON structuré des enregistrements de changement comme seule source.
2) Agréger les explications en points concis par ADDED, MODIFIED, REMOVED.
3) Omettre les groupes sans élément.

Format de sortie (Markdown) :
- **ADDED**
  - <point>
- **MODIFIED**
  - <point>
- **REMOVED**
  - <point>

Règles :
- Rester bref et précis.
- Mentionner les sections si disponibles.
- Inclure les implications table/formule/équation seulement si présentes dans le JSON.
- Ne pas inventer d’informations hors JSON.
- Écrire en français.

Document A : {doc1_name}
Document B : {doc2_name}

JSON des enregistrements de changement :
{explanations}
""".strip()

SUMMARIZE_EXPLANATIONS_PROMPTS = {
    "en": PROMPT_SUMMARIZE_EXPLANATIONS_EN,
    "de": PROMPT_SUMMARIZE_EXPLANATIONS_DE,
    "fr": PROMPT_SUMMARIZE_EXPLANATIONS_FR,
}

PROMPT_FOLLOWUPS_EN = """

Generate up to {max_questions} follow-up questions based ONLY on the diff list.
Do NOT invent sections not present.
Write in English.

Document A: {doc1_name}
Document B: {doc2_name}

Diffs JSON:
{diffs}

Return as a numbered list (1., 2., 3., ...). Keep questions specific and actionable.
For EMC test-level changes: ask whether existing type approvals or test reports are still valid.
For test method changes: ask whether the test laboratory is accredited under the new method.
For acceptance criterion changes: ask which product variants or ports are affected.
""".strip()

PROMPT_FOLLOWUPS_DE = """

Erstellen Sie bis zu {max_questions} Folgefragen ausschließlich auf Basis der Diff-Liste.
Erfinden Sie keine Abschnitte, die nicht enthalten sind.
Schreiben Sie auf Deutsch.

Dokument A: {doc1_name}
Dokument B: {doc2_name}

Diffs JSON:
{diffs}

Geben Sie die Fragen als nummerierte Liste zurück (1., 2., 3., ...). Die Fragen sollen konkret und umsetzbar sein.
""".strip()

PROMPT_FOLLOWUPS_FR = """

Générez jusqu’à {max_questions} questions de suivi en vous basant UNIQUEMENT sur la liste des diffs.
N’inventez pas de sections absentes.
Écrivez en français.

Document A : {doc1_name}
Document B : {doc2_name}

Diffs JSON :
{diffs}

Retournez une liste numérotée (1., 2., 3., ...). Les questions doivent être spécifiques et actionnables.
""".strip()

FOLLOWUP_PROMPTS = {
    "en": PROMPT_FOLLOWUPS_EN,
    "de": PROMPT_FOLLOWUPS_DE,
    "fr": PROMPT_FOLLOWUPS_FR,
}

PROMPT_MARKDOWN_DIFF_CLAUSE = """
You are an EMC compliance auditor. Produce a brief semantic diff in markdown. Change type: {change_type}.

{source_block}

Rules — follow strictly:
- Semantic changes ONLY: obligations, conditions, scope, numeric values (test levels in V/m or dBµV, frequency ranges in MHz/GHz, acceptance classes A–E), IEC/CISPR/ISO standard references, test procedure steps, dwell times, named entities, actions.
- IGNORE cosmetic differences: whitespace, punctuation, capitalisation, rephrasing with identical meaning.
- If no semantic change is detectable, output nothing at all.
- Do NOT mention tables, cells, rows, columns, or node types under any circumstance.
- No explanations, commentary, or notes about what is or is not applicable.
- Write in the same language as the source text.

Output EXACTLY ONE of the following formats — never combine them:

Format A — inline bullets (word/phrase-level changes):
- Removed: <span style="color:red">~~phrase~~</span>
- Added: <span style="color:green">**phrase**</span>
- Modified: <span style="color:red">~~old~~</span> → <span style="color:green">**new**</span>

Format B — fenced diff block (sentence/line-level changes):
```diff
- removed sentence or line
+ added sentence or line
```
""".strip()

PROMPT_MARKDOWN_DIFF_TABLE = """
Produce a concise semantic diff for a TABLE change. Change type: {change_type}.

{source_block}

Rules — follow strictly:
- Identify EXACTLY what changed: which cells, rows, or columns, and what the old vs new values are.
- State the compliance significance of the change (e.g. threshold tightened, scope expanded, control removed).
- IGNORE cosmetic differences: whitespace, punctuation, capitalisation with identical meaning.
- If no semantic change is detectable, output nothing at all.
- No generic commentary like "the table was modified". Be specific.
- Write in the same language as the source text.

Output format — use one or both as needed:

Cell/row-level changes:
- `<header> / Row <N>:` <span style="color:red">~~old value~~</span> → <span style="color:green">**new value**</span>

Added/removed rows:
```diff
- removed row: col1 | col2 | col3
+ added row: col1 | col2 | col3
```

End with one sentence on compliance significance, e.g.:
> This tightens the minimum password length requirement from 8 to 12 characters.
""".strip()

# Back-compat alias used by tests that import this name directly.
PROMPT_MARKDOWN_DIFF_SUMMARY = PROMPT_MARKDOWN_DIFF_CLAUSE

PROMPT_TABLE_STRUCTURED_DIFF = """
You are an expert EMC compliance auditor reviewing a table change. Change type: {change_type}.

Structured cell changes (JSON):
{changed_cells_json}

Old table:
{old_table}

New table:
{new_table}

Rules — follow strictly:
- Lead with the most compliance-significant change.
- Name specific column and row: "`<Column>` / Row <N>: ~~old~~ → **new**"
- For added/removed rows: describe what the row represents in context.
- IGNORE cosmetic differences: whitespace, punctuation, capitalisation with identical meaning.
- If no semantic change is detectable, output nothing at all.
- End with one sentence on compliance significance, e.g.:
  > This tightens the minimum password length requirement from 8 to 12 characters.
  > This increases the radiated immunity test level from 10 V/m to 30 V/m at 80–1000 MHz, requiring re-qualification.
  > This changes the acceptance criterion from Class B to Class A, imposing stricter limits.
- Write in {language}.
""".strip()

PROMPT_CHANGE_RECORD_JSON = """
{role}

You are comparing two versions of a compliance standard/policy. For the SINGLE change below,
produce a structured change record that a tester can act on.

Change metadata:
- change_id: {change_id}
- node_type: {node_type}
- change_type: {change_type}

Before (doc1):
{doc1_text}

After (doc2):
{doc2_text}

Allowed `change_type` values (choose ONE):
- limit_changed
- test_added
- test_removed
- acceptance_changed
- sample_requirement_changed
- referenced_standard_changed
- scope_changed
- applicability_changed
- exemption_changed
- product_class_changed
- definition_changed
- editorial_only
- uncertain

Allowed `status` values (choose ONE):
- Accepted
- Needs human review
- Low-confidence extraction
- Potentially editorial
- Potentially safety-critical

Allowed `normativity` values (choose ONE):
- normative
- informative
- note
- example
- unknown

Rules — follow strictly:
- Use ONLY the Before/After text above.
- If you cannot confidently classify, use change_type="uncertain" and status="Needs human review".
- If the change is purely editorial (formatting/wording with identical meaning), use change_type="editorial_only"
  and status="Potentially editorial".
- Prefer quoting exact old/new values (including units) when present.
- `old_value` / `new_value` may be empty strings when not applicable or not explicit.
- `tester_action` must be an imperative sentence (e.g. "Update EMC test level to ...", "Re-run ingress test at ...").
- `retest_likely` must be true if retesting is plausibly required based on the text change.
- `normativity`: set to "normative" if text contains shall/must/is required/muss/ist zu, else "informative" or "note"/"example" as appropriate.
- `extraction_confidence`: [0,1] — how cleanly the old/new values could be extracted (lower if text is ambiguous, truncated, or table-based with uncertain structure).
- `alignment_confidence`: [0,1] — how confident you are that Before and After are actually comparing the same requirement (lower if subjects differ or context is unclear).
- `change_confidence`: [0,1] — how confident you are in the change classification.
- `impact_confidence`: [0,1] — how confident you are that the stated tester_action is the correct response.

Output MUST be a single valid JSON object ONLY (no markdown, no code fences, no extra text) with EXACT keys:
change_id, change_type, old_value, new_value, tester_action, retest_likely, status,
normativity, extraction_confidence, alignment_confidence, change_confidence, impact_confidence
""".strip()


PROMPT_DIFF_TABLE_ROW = """
{role_line}

You are reviewing a change in a compliance document.

Section: {section}
Page: {page}
Change type: {change_type}

{source_block}

Your task: Write a single plain-text sentence (or two at most) explaining the SEMANTIC difference
from the perspective of a {department} tester or auditor.
Focus on what changed in terms of requirements, limits, procedures, or applicability.

Rules — follow strictly:
- Output ONLY the semantic explanation. No markdown, no bullets, no headers, no JSON.
- If there is NO semantic difference (purely cosmetic/whitespace/formatting change), output exactly: SKIP
- If you cannot confidently determine the semantic difference, output: HUMAN_REVIEW: <brief reason>
- Do not invent changes not present in the source text.
- Include specific values (frequencies, limits, units) when present.
""".strip()


@dataclass(frozen=True)
class OllamaSettings:
    base_url: str = "http://localhost:11434"
    chat_model: str = "granite3.3:8b"
    embed_model: str = "qwen3-embedding"
    connect_timeout_sec: float = 10.0
    read_timeout_sec: float = 600.0
    write_timeout_sec: float = 60.0
    max_retries: int = 2
    opik_enabled: bool = False
    opik_url: str = "http://localhost:5173/api"
    opik_project_name: str = "grc-policy-server"
    opik_workspace: str = "default"

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
        tracing.configure(
            enabled=self.settings.opik_enabled,
            url=self.settings.opik_url,
            project_name=self.settings.opik_project_name,
            workspace=self.settings.opik_workspace,
        )
        self._async_client = httpx.AsyncClient(
            base_url=self.settings.base_url,
            timeout=httpx.Timeout(
                connect=self.settings.connect_timeout_sec,
                read=self.settings.read_timeout_sec,
                write=self.settings.write_timeout_sec,
                pool=self.settings.connect_timeout_sec,
            ),
        )

    async def embed(self, text: str) -> list[float]:
        tracked = tracing.track(
            name="ollama_embed",
            type="llm",
            tags=["llm", "ollama", "embedding"],
            metadata={"model": self.settings.embed_model},
            project_name=self.settings.opik_project_name,
        )(self._embed_untraced)
        return await tracked(text)

    async def _embed_untraced(self, text: str) -> list[float]:
        payload = {
            "model": self.settings.embed_model,
            "input": text,
        }
        response = await self._post_json("/api/embed", payload)
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
        testing_department: str | None = None,
    ) -> str:
        compact = self._compact_diffs_for_summary(key_differences)
        prompt = self._prompt_summarize_changes(
            doc1_name=doc1_name, doc2_name=doc2_name, diffs=compact, language=language
        )
        dept = normalize_testing_department(testing_department)
        if dept != "EMC":
            profile = get_testing_department_profile(dept)
            prompt = (
                f"IMPORTANT CONTEXT (Department={profile.department}): {profile.role}\n\n"
                + prompt
            )
        return await self._generate_text(prompt, temperature=0.5)

    async def summarize_explanations(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        explanations: List[Dict[str, str]],
        language: str = "",
    ) -> str:
        prompt = self._prompt_summarize_explanations(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            explanations=explanations,
            language=language,
        )
        return await self._generate_text(prompt, temperature=0.4)

    async def generate_followups(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
        max_questions: int = 4,
        language: str = "",
        testing_department: str | None = None,
    ) -> List[str]:
        compact = self._compact_diffs_for_followups(key_differences)
        prompt = self._prompt_followups(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            diffs=compact,
            max_questions=max_questions,
            language=language,
        )
        dept = normalize_testing_department(testing_department)
        if dept != "EMC":
            profile = get_testing_department_profile(dept)
            prompt = (
                f"IMPORTANT CONTEXT (Department={profile.department}): {profile.role}\n\n"
                + prompt
            )
        text = await self._generate_text(prompt, temperature=0.6)
        return self._parse_numbered_questions(text, max_questions=max_questions)

    _NO_CHANGE_MARKERS = frozenset(
        {"(no semantic change)", "(no change)", "no semantic change", "no change"}
    )

    async def generate_markdown_diff_summary(
        self,
        *,
        node_type: str,
        change_type: str,
        doc1_source_text: str | None,
        doc2_source_text: str | None,
        doc1_table_content: str | None = None,
        doc2_table_content: str | None = None,
        language: str = "",
        testing_department: str | None = None,
    ) -> str:
        prompt = self._prompt_markdown_diff_summary(
            node_type=node_type,
            change_type=change_type,
            doc1_source_text=doc1_source_text,
            doc2_source_text=doc2_source_text,
            doc1_table_content=doc1_table_content,
            doc2_table_content=doc2_table_content,
            language=language,
            testing_department=testing_department,
        )
        result = await self._generate_text(prompt, temperature=0)
        # Return empty string when the LLM signals no semantic change, so
        # _populate_markdown_diff_summaries can set markdownDiffSummary = None.
        if result.strip().lower().strip("().") in self._NO_CHANGE_MARKERS:
            return ""
        return result

    async def generate_change_record_json(
        self,
        *,
        change_id: str,
        node_type: str,
        change_type: str,
        doc1_source_text: str | None,
        doc2_source_text: str | None,
        language: str = "",
        testing_department: str | None = None,
    ) -> str:
        prompt = self._prompt_change_record_json(
            change_id=change_id,
            node_type=node_type,
            change_type=change_type,
            doc1_source_text=doc1_source_text,
            doc2_source_text=doc2_source_text,
            language=language,
            testing_department=testing_department,
        )
        return await self._generate_text(prompt, temperature=0.0)

    async def generate_diff_table_row_stream(
        self,
        *,
        section: str,
        page: int | None,
        change_type: str,
        node_type: str,
        doc1_text: str | None,
        doc2_text: str | None,
        doc1_table_md: str | None = None,
        doc2_table_md: str | None = None,
        testing_department: str | None = None,
        language: str = "",
    ):
        prompt = self._prompt_diff_table_row(
            section=section,
            page=page,
            change_type=change_type,
            node_type=node_type,
            doc1_text=doc1_text,
            doc2_text=doc2_text,
            doc1_table_md=doc1_table_md,
            doc2_table_md=doc2_table_md,
            testing_department=testing_department,
            language=language,
        )
        result = await self._generate_text(prompt, temperature=0.0)
        if result.strip():
            yield result
        else:
            yield "SKIP"

    async def explain_table_diff(
        self,
        *,
        old_markdown: str | None,
        new_markdown: str | None,
        changed_cells: list[dict],
        change_type: str,
        language: str = "",
    ) -> str:
        import json as _json

        if not changed_cells and not old_markdown and not new_markdown:
            return ""
        lang_display = self._language_hint(language).strip() or "the same language as the source text"
        prompt = PROMPT_TABLE_STRUCTURED_DIFF.format(
            change_type=change_type,
            changed_cells_json=_json.dumps(changed_cells, ensure_ascii=False, indent=2),
            old_table=old_markdown or "(not present)",
            new_table=new_markdown or "(not present)",
            language=lang_display,
        )
        result = await self._generate_text(prompt, temperature=0)
        if result.strip().lower().strip("().") in self._NO_CHANGE_MARKERS:
            return ""
        return result

    def close(self) -> None:
        """Sync best-effort close. Use aclose() in async contexts."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_client.aclose())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._async_client.aclose())
            finally:
                loop.close()

    async def aclose(self) -> None:
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

    async def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            if attempt > 0:
                backoff = min(2.0 ** attempt, 30.0)
                await asyncio.sleep(random.uniform(backoff * 0.75, backoff * 1.25))
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
        lang_code = language if language in SUMMARIZE_DIFF_PROMPTS else "en"
        return SUMMARIZE_DIFF_PROMPTS[lang_code].format(
            section=section,
            old_text=old_text,
            new_text=new_text,
        )

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
            sort_keys=True,
        )
        return PROMPTS[lang_code].format(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            diffs=canonical_diffs,
        )

    def _prompt_summarize_explanations(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        explanations: List[Dict[str, str]],
        language: str = "",
    ) -> str:
        lang_code = language if language in SUMMARIZE_EXPLANATIONS_PROMPTS else "en"
        canonical_explanations = json.dumps(
            explanations[:80],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return SUMMARIZE_EXPLANATIONS_PROMPTS[lang_code].format(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            explanations=canonical_explanations,
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
        lang_code = language if language in FOLLOWUP_PROMPTS else "en"
        canonical_diffs = json.dumps(
            diffs,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return FOLLOWUP_PROMPTS[lang_code].format(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            diffs=canonical_diffs,
            max_questions=max_questions,
        )

    _LANGUAGE_NAMES = {"en": "English", "de": "German (Deutsch)", "fr": "French (Français)"}

    def _prompt_markdown_diff_summary(
        self,
        *,
        node_type: str,
        change_type: str,
        doc1_source_text: str | None,
        doc2_source_text: str | None,
        doc1_table_content: str | None = None,
        doc2_table_content: str | None = None,
        language: str = "",
        testing_department: str | None = None,
    ) -> str:
        if node_type == "table":
            left = doc1_table_content or doc1_source_text or ""
            right = doc2_table_content or doc2_source_text or ""
            if change_type == "ADDED":
                source_block = f"Added table content:\n{right}"
            elif change_type == "REMOVED":
                source_block = f"Removed table content:\n{left}"
            else:
                source_block = f"Table before:\n{left}\n\nTable after:\n{right}"
        else:
            if change_type == "ADDED":
                source_block = f"Added content:\n{doc2_source_text or ''}"
            elif change_type == "REMOVED":
                source_block = f"Removed content:\n{doc1_source_text or ''}"
            else:  # MODIFIED
                source_block = (
                    f"Before:\n{doc1_source_text or ''}\n\n"
                    f"After:\n{doc2_source_text or ''}"
                )
        template = (
            PROMPT_MARKDOWN_DIFF_TABLE
            if node_type == "table"
            else PROMPT_MARKDOWN_DIFF_CLAUSE
        )
        dept = normalize_testing_department(testing_department)
        if dept != "EMC":
            profile = get_testing_department_profile(dept)
            if node_type == "table":
                template = template.replace(
                    "Produce a concise semantic diff for a TABLE change.",
                    f"{profile.role}\n\nProduce a concise semantic diff for a TABLE change.",
                )
            else:
                template = template.replace(
                    "You are an EMC compliance auditor.",
                    profile.role,
                )
        prompt = template.format(
            change_type=change_type,
            source_block=source_block,
        )
        lang_name = self._LANGUAGE_NAMES.get(language)
        if lang_name:
            return (
                f"IMPORTANT: You MUST write your entire response in {lang_name} only.\n\n"
                + prompt
            )
        return prompt

    def _prompt_change_record_json(
        self,
        *,
        change_id: str,
        node_type: str,
        change_type: str,
        doc1_source_text: str | None,
        doc2_source_text: str | None,
        language: str = "",
        testing_department: str | None = None,
    ) -> str:
        _ = language  # reserved for future localized status/action text
        profile = get_testing_department_profile(testing_department)
        doc1_text = (doc1_source_text or "").strip() or "(not present)"
        doc2_text = (doc2_source_text or "").strip() or "(not present)"
        return PROMPT_CHANGE_RECORD_JSON.format(
            role=f"Department={profile.department}. {profile.role}\nFocus: "
            + "; ".join(profile.focus),
            change_id=change_id,
            node_type=node_type,
            change_type=change_type,
            doc1_text=doc1_text,
            doc2_text=doc2_text,
        )

    def _prompt_diff_table_row(
        self,
        *,
        section: str,
        page: int | None,
        change_type: str,
        node_type: str,
        doc1_text: str | None,
        doc2_text: str | None,
        doc1_table_md: str | None = None,
        doc2_table_md: str | None = None,
        testing_department: str | None = None,
        language: str = "",
    ) -> str:
        profile = get_testing_department_profile(testing_department)
        dept = normalize_testing_department(testing_department)
        role_line = profile.role

        if node_type == "table":
            left = doc1_table_md or doc1_text or ""
            right = doc2_table_md or doc2_text or ""
            if change_type == "ADDED":
                source_block = f"New table content:\n{right}"
            elif change_type == "REMOVED":
                source_block = f"Removed table content:\n{left}"
            else:
                source_block = f"Table before:\n{left}\n\nTable after:\n{right}"
        else:
            if change_type == "ADDED":
                source_block = f"New clause:\n{doc2_text or ''}"
            elif change_type == "REMOVED":
                source_block = f"Removed clause:\n{doc1_text or ''}"
            else:
                source_block = (
                    f"Before:\n{doc1_text or ''}\n\nAfter:\n{doc2_text or ''}"
                )

        page_str = f"p.{page}" if page is not None else "—"
        prompt = PROMPT_DIFF_TABLE_ROW.format(
            role_line=role_line,
            section=section or "—",
            page=page_str,
            change_type=change_type,
            department=dept,
            source_block=source_block,
        )
        lang_name = self._LANGUAGE_NAMES.get(language)
        if lang_name:
            return (
                f"IMPORTANT: You MUST write your entire response in {lang_name} only.\n\n"
                + prompt
            )
        return prompt

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
                    "nodeType": d.nodeType,
                    "impact": d.impact,
                    "changeSeverity": d.changeSeverity,
                    "doc1Content": d.doc1Content,
                    "doc2Content": d.doc2Content,
                    "changes": [
                        change.model_dump(mode="json") for change in d.changes
                    ],
                    "doc1Citation": (
                        d.doc1Reference.model_dump(mode="json")
                        if d.doc1Reference
                        else None
                    ),
                    "doc2Citation": (
                        d.doc2Reference.model_dump(mode="json")
                        if d.doc2Reference
                        else None
                    ),
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
- object: The target, value, or content the action applies to (include specific numbers, dates, names, quantities, or key terms; for EMC requirements capture test levels e.g. "10 V/m 80–1000 MHz", standard references with edition e.g. "IEC 61000-4-3 Ed.3.2", and acceptance classes e.g. "Class A")
- condition: Any qualifiers, constraints, scope, context, exceptions, timeframes, or prerequisites; for EMC include dwell time, measurement cycles, step size, test sequence prerequisites, and equipment specifications

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
        temp = 0 if temperature is None else temperature
        tracked = tracing.track(
            name="ollama_generate",
            type="llm",
            tags=["llm", "ollama", "generation"],
            metadata={"model": self.settings.chat_model, "temperature": temp},
            project_name=self.settings.opik_project_name,
        )(self._generate_text_untraced)
        return await tracked(prompt, temp)

    async def _generate_text_untraced(self, prompt: str, temperature: float) -> str:
        response = await self._post_json(
            "/api/generate",
            {
                "model": self.settings.chat_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature},
            },
        )
        text = str(response.get("response") or "").strip()
        # Strip chain-of-thought tags emitted by granite/deepseek style models.
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return text
