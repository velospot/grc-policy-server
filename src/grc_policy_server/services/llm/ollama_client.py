# src/grc_policy_server/services/llm/ollama_client.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
from langchain_ollama import ChatOllama, OllamaEmbeddings

from grc_policy_server.core.logging import logging
from grc_policy_server.models.schemas import KeyDifference
from grc_policy_server.services.llm.base import BaseLLM

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OllamaSettings:
    base_url: str = "http://192.168.178.23:11434"
    chat_model: str = "granite3.3:8b"
    embed_model: str = "qwen3-embedding"


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

    def embed(self, text: str) -> list[float]:

        llm = OllamaEmbeddings(
            model=self.settings.embed_model, base_url=self.settings.base_url
        )
        emb = llm.embed_query(text)

        return emb

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

        llm = ChatOllama(
            model=self.settings.chat_model,
            base_url=self.settings.base_url,
            # other params...
        )
        ai_msg = await llm.ainvoke(prompt)
        return str(ai_msg.content)

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

        llm = ChatOllama(
            model=self.settings.chat_model,
            base_url=self.settings.base_url,
        )
        ai_msg = await llm.ainvoke(prompt)
        return str(ai_msg.content)

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

        llm = ChatOllama(
            model=self.settings.chat_model,
            base_url=self.settings.base_url,
        )
        ai_msg = await llm.ainvoke(prompt)
        text = str(ai_msg.content)
        return self._parse_numbered_questions(text, max_questions=max_questions)

    # -------------------------
    # HTTP helpers
    # -------------------------

    async def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.settings.base_url}{path}"
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"Ollama response is not JSON object: {data}")
            return data

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
You are a compliance analyst. Summarize changes between two document versions.

Document A: {doc1_name}
Document B: {doc2_name}

You MUST follow these rules:
- Use ONLY the provided differences JSON.
- Do NOT invent changes, sections, or impacts not present.
- Prefer clear, auditor-friendly language.
- Keep it to 3–6 sentences.

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
You are a GRC auditor.

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
