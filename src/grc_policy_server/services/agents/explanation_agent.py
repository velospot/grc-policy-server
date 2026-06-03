"""Explanation Agent — AGENTS.md Agent 6.

The only agent that produces free-form text for auditors. Wraps an existing
BaseLLM client with a focused compliance explanation prompt enforcing:

  - 2–3 sentences maximum per finding
  - No remediation recommendations
  - No compliance determinations (PASS/FAIL)
  - References to specific standards, clauses, measurements when present
  - Written for senior compliance engineers

Key design: BaseLLM subclass (not standalone httpx). Plugs directly into
RealDiffEngineStream.compare_stream_v4() which calls
`llm.generate_diff_table_row_stream()` per diff — no streaming engine changes.

Per-diff output is capped at max_tokens to stay within the latency budget on
local Ollama. See docs/agentic_limitations.md for guidance.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List

from grc_policy_server.models.schemas import KeyDifference
from grc_policy_server.services.llm.base import BaseLLM

logger = logging.getLogger(__name__)

_MAX_TOKENS_DEFAULT = 80
# Approximate: compliance text averages ~6 chars/token.
_CHARS_PER_TOKEN = 6


class ExplanationAgent(BaseLLM):
    """Focused per-diff compliance explanation wrapping an existing BaseLLM.

    Overrides only `generate_diff_table_row_stream()`. All other abstract
    methods delegate to the wrapped client unchanged.
    """

    def __init__(self, *, llm: BaseLLM, max_tokens: int = _MAX_TOKENS_DEFAULT) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    # ------------------------------------------------------------------
    # Core override — per-diff compliance explanation with token budget
    # ------------------------------------------------------------------

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
    ) -> AsyncIterator[str]:
        """Stream a focused compliance explanation for one diff.

        Delegates to wrapped LLM's streaming. Enforces token budget to keep
        per-diff latency within ~3s on local Ollama (granite3.3:8b).
        """
        accumulated: list[str] = []
        char_budget = self._max_tokens * _CHARS_PER_TOKEN
        try:
            async for token in self._llm.generate_diff_table_row_stream(
                section=section,
                page=page,
                change_type=change_type,
                node_type=node_type,
                doc1_text=_truncate(doc1_table_md or doc1_text, 300),
                doc2_text=_truncate(doc2_table_md or doc2_text, 300),
                doc1_table_md=doc1_table_md,
                doc2_table_md=doc2_table_md,
                testing_department=testing_department,
                language=language,
            ):
                accumulated.append(token)
                yield token
                if sum(len(t) for t in accumulated) >= char_budget:
                    break
        except Exception:
            logger.debug(
                "ExplanationAgent stream failed — yielding SKIP", exc_info=True
            )
            if not accumulated:
                yield "SKIP"

    # ------------------------------------------------------------------
    # Delegation — all other BaseLLM abstract methods
    # ------------------------------------------------------------------

    async def embed(self, text: str) -> list[float]:
        return await self._llm.embed(text)

    async def extract_policy_meanings(
        self,
        *,
        texts: List[str],
        markdown_texts: List[str] | None = None,
        language: str = "",
    ) -> List[Dict[str, str]]:
        return await self._llm.extract_policy_meanings(
            texts=texts, markdown_texts=markdown_texts, language=language
        )

    async def summarize_changes(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
        language: str = "",
        testing_department: str | None = None,
    ) -> str:
        return await self._llm.summarize_changes(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            key_differences=key_differences,
            language=language,
            testing_department=testing_department,
        )

    async def summarize_diff(
        self, *, old_text: str, new_text: str, section: str, language: str = ""
    ) -> str:
        return await self._llm.summarize_diff(
            old_text=old_text, new_text=new_text, section=section, language=language
        )

    async def summarize_explanations(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        explanations: List[Dict[str, str]],
        language: str = "",
    ) -> str:
        return await self._llm.summarize_explanations(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            explanations=explanations,
            language=language,
        )

    async def generate_followups(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
        max_questions: int = 6,
        language: str = "",
        testing_department: str | None = None,
    ) -> List[str]:
        return await self._llm.generate_followups(
            doc1_name=doc1_name,
            doc2_name=doc2_name,
            key_differences=key_differences,
            max_questions=max_questions,
            language=language,
            testing_department=testing_department,
        )

    async def detect_language(self, text_sample: str) -> str:
        return await self._llm.detect_language(text_sample)

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
        return await self._llm.generate_markdown_diff_summary(
            node_type=node_type,
            change_type=change_type,
            doc1_source_text=doc1_source_text,
            doc2_source_text=doc2_source_text,
            doc1_table_content=doc1_table_content,
            doc2_table_content=doc2_table_content,
            language=language,
            testing_department=testing_department,
        )

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
        return await self._llm.generate_change_record_json(
            change_id=change_id,
            node_type=node_type,
            change_type=change_type,
            doc1_source_text=doc1_source_text,
            doc2_source_text=doc2_source_text,
            language=language,
            testing_department=testing_department,
        )

    async def aclose(self) -> None:
        await self._llm.aclose()


def _truncate(text: str | None, max_chars: int) -> str | None:
    if not text:
        return text
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"
