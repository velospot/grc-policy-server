# src/grc_policy_server/services/llm/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Dict, List

from grc_policy_server.models.schemas import KeyDifference


class BaseLLM(ABC):
    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Return embedding vector for text."""
        raise NotImplementedError

    def close(self) -> None:
        """Best-effort sync close hook (optional)."""
        return

    async def aclose(self) -> None:
        """Best-effort async close hook (optional)."""
        self.close()

    @abstractmethod
    async def extract_policy_meanings(
        self,
        *,
        texts: List[str],
        markdown_texts: List[str] | None = None,
        language: str = "",
    ) -> List[Dict[str, str]]:
        """
        Extract normalized clause meaning for policy statements in any language.
        The returned list must preserve input order.
        ``markdown_texts`` is an optional parallel list of markdown-formatted
        versions of the same clauses; when provided, implementations should use
        them to improve structure understanding (headers, lists, emphasis).
        Pass language code ('en', 'de', 'fr') for better accuracy.
        """
        raise NotImplementedError

    @abstractmethod
    async def summarize_changes(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
        language: str = "",
        testing_department: str | None = None,
    ) -> str:
        """
        Generate an executive summary of changes based strictly on provided diffs.
        Must not invent changes beyond the diffs passed in.
        Pass language code ('en', 'de', 'fr') for better accuracy.
        """
        raise NotImplementedError

    @abstractmethod
    async def summarize_diff(
        self,
        *,
        old_text: str,
        new_text: str,
        section: str,
        language: str = "",
    ) -> str:
        """
        Summarize the change for a single chunk pair (for MODIFIED items).
        Must not introduce facts not present in old/new.
        Pass language code ('en', 'de', 'fr') for better accuracy.
        """
        raise NotImplementedError

    @abstractmethod
    async def summarize_explanations(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        explanations: List[Dict[str, str]],
        language: str = "",
    ) -> str:
        """
        Aggregate per-change explanations into a concise executive summary
        grouped by ADDED / MODIFIED / REMOVED.
        Must not invent facts beyond the explanations passed in.
        Pass language code ('en', 'de', 'fr') for better accuracy.
        """
        raise NotImplementedError

    @abstractmethod
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
        """
        Generate follow-up questions an auditor should ask, based only on diffs.
        Pass language code ('en', 'de', 'fr') for better accuracy.
        """
        raise NotImplementedError

    @abstractmethod
    async def detect_language(self, text_sample: str) -> str:
        """
        Detect the language of a document from a text sample.
        Returns language code: 'en' (English), 'de' (German), 'fr' (French), or 'unknown'.
        """
        raise NotImplementedError

    @abstractmethod
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
        """
        Generate a markdown-formatted diff summary for a single change.
        Strictly highlights what changed using markdown (bold, color, strikethrough).
        Not creative — only reflects what is present in the source texts.
        ``doc1_table_content`` / ``doc2_table_content`` are pre-rendered table
        strings for node_type == "table"; when provided they replace source text
        for the diff prompt so the LLM sees row/cell structure rather than raw HTML.
        Output is written in the same language as the source text (language hint
        improves accuracy when the detected language code is passed in).
        Returns an empty string when no semantic change is detected.
        """
        raise NotImplementedError

    async def generate_markdown_diff_summary_stream(
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
    ) -> AsyncIterator[str]:
        """Yield tokens for a markdown diff summary.

        Default: awaits the full response and yields it as a single token.
        VllmClient overrides with true SSE token streaming.
        """
        result = await self.generate_markdown_diff_summary(
            node_type=node_type,
            change_type=change_type,
            doc1_source_text=doc1_source_text,
            doc2_source_text=doc2_source_text,
            doc1_table_content=doc1_table_content,
            doc2_table_content=doc2_table_content,
            language=language,
            testing_department=testing_department,
        )
        if result:
            yield result

    @abstractmethod
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
        """Generate a structured change record as a JSON object string."""
        raise NotImplementedError

    async def generate_change_record_json_stream(
        self,
        *,
        change_id: str,
        node_type: str,
        change_type: str,
        doc1_source_text: str | None,
        doc2_source_text: str | None,
        language: str = "",
        testing_department: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield tokens for a change-record JSON string.

        Default: awaits the full response and yields it as a single token.
        """
        result = await self.generate_change_record_json(
            change_id=change_id,
            node_type=node_type,
            change_type=change_type,
            doc1_source_text=doc1_source_text,
            doc2_source_text=doc2_source_text,
            language=language,
            testing_department=testing_department,
        )
        if result:
            yield result

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
        """Stream a single-sentence semantic explanation for one diff table row.

        Implementations should yield tokens of a plain-text sentence (no markdown).
        The sentinel values ``SKIP`` and ``HUMAN_REVIEW: <reason>`` are valid
        complete responses — callers must check for them before rendering.

        Default: delegates to generate_markdown_diff_summary_stream and maps an
        empty response to ``SKIP``.
        """
        tokens: list[str] = []
        async for token in self.generate_markdown_diff_summary_stream(
            node_type=node_type,
            change_type=change_type,
            doc1_source_text=doc1_text,
            doc2_source_text=doc2_text,
            doc1_table_content=doc1_table_md,
            doc2_table_content=doc2_table_md,
            language=language,
            testing_department=testing_department,
        ):
            tokens.append(token)
            yield token
        if not "".join(tokens).strip():
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
        """Explain table changes using structured cell-level data.

        ``changed_cells`` is a list of dicts with keys:
          type, text, oldValue, newValue, location, header (column name).
        Default implementation falls back to generate_markdown_diff_summary() so
        subclasses only need to override when a richer prompt is available.
        """
        return await self.generate_markdown_diff_summary(
            node_type="table",
            change_type=change_type,
            doc1_source_text=None,
            doc2_source_text=None,
            doc1_table_content=old_markdown,
            doc2_table_content=new_markdown,
            language=language,
        )
