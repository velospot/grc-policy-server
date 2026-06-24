"""No-op LLM stub for offline mode.

All methods return trivially-safe empty values so the rest of the comparison
pipeline continues without error. The OfflineDiffEngine overrides the methods
that need real output (summaries, follow-up questions) directly, so this stub
only needs to satisfy the abstract interface — not produce useful text.
"""
from __future__ import annotations

from typing import AsyncIterator, Dict, List

from grc_policy_server.models.schemas import KeyDifference
from grc_policy_server.services.llm.base import BaseLLM


class NoOpLLM(BaseLLM):
    """Zero-dependency implementation of BaseLLM used by OfflineDiffEngine."""

    async def embed(self, text: str) -> list[float]:
        return []

    async def extract_policy_meanings(
        self,
        *,
        texts: List[str],
        markdown_texts: List[str] | None = None,
        language: str = "",
    ) -> List[Dict[str, str]]:
        return [{} for _ in texts]

    async def summarize_changes(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
        language: str = "",
        testing_department: str | None = None,
    ) -> str:
        return ""

    async def summarize_diff(
        self,
        *,
        old_text: str,
        new_text: str,
        section: str,
        language: str = "",
    ) -> str:
        return ""

    async def summarize_explanations(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        explanations: List[Dict[str, str]],
        language: str = "",
    ) -> str:
        return ""

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
        return []

    async def detect_language(self, text_sample: str) -> str:
        return "en"

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
        return ""

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
        return "{}"

    async def generate_markdown_diff_summary_stream(  # type: ignore[override]
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
        # Async generator that yields nothing (empty stream).
        return
        yield  # pragma: no cover

    async def generate_change_record_json_stream(  # type: ignore[override]
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
        return
        yield  # pragma: no cover
