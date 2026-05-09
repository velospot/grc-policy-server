from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine, TypeVar

import httpx

from grc_policy_server.core.logging import logging
from grc_policy_server.services.llm.base import BaseLLM

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _should_fallback(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.HTTPError, OSError, ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        return any(
            token in msg
            for token in (
                "timed out",
                "timeout",
                "connection",
                "connect",
                "refused",
                "unreachable",
                "dns",
            )
        )
    return False


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value


class FallbackLLM(BaseLLM):
    """Try a primary LLM backend, fallback to a secondary on transport failures."""

    def __init__(self, *, primary: BaseLLM, fallback: BaseLLM) -> None:
        self.primary = primary
        self.fallback = fallback

    async def embed(self, text: str) -> list[float]:
        try:
            return await self.primary.embed(text)
        except Exception as exc:
            if not _should_fallback(exc):
                raise
            logger.warning("primary llm embed failed; falling back: %s", exc)
            return await self.fallback.embed(text)

    async def extract_policy_meanings(
        self,
        *,
        texts: list[str],
        markdown_texts: list[str] | None = None,
        language: str = "",
    ):
        return await self._call_async(
            lambda llm: llm.extract_policy_meanings(
                texts=texts,
                markdown_texts=markdown_texts,
                language=language,
            ),
            name="extract_policy_meanings",
        )

    async def summarize_explanations(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        explanations: list[dict],
        language: str = "",
    ) -> str:
        return await self._call_async(
            lambda llm: llm.summarize_explanations(
                doc1_name=doc1_name,
                doc2_name=doc2_name,
                explanations=explanations,
                language=language,
            ),
            name="summarize_explanations",
        )

    async def summarize_changes(
        self, *, doc1_name: str, doc2_name: str, key_differences, language: str = ""
    ) -> str:
        return await self._call_async(
            lambda llm: llm.summarize_changes(
                doc1_name=doc1_name,
                doc2_name=doc2_name,
                key_differences=key_differences,
                language=language,
            ),
            name="summarize_changes",
        )

    async def summarize_diff(
        self, *, old_text: str, new_text: str, section: str, language: str = ""
    ) -> str:
        return await self._call_async(
            lambda llm: llm.summarize_diff(
                old_text=old_text,
                new_text=new_text,
                section=section,
                language=language,
            ),
            name="summarize_diff",
        )

    async def generate_followups(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences,
        max_questions: int = 6,
        language: str = "",
    ) -> list[str]:
        return await self._call_async(
            lambda llm: llm.generate_followups(
                doc1_name=doc1_name,
                doc2_name=doc2_name,
                key_differences=key_differences,
                max_questions=max_questions,
                language=language,
            ),
            name="generate_followups",
        )

    async def detect_language(self, text_sample: str) -> str:
        return await self._call_async(
            lambda llm: llm.detect_language(text_sample),
            name="detect_language",
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
    ) -> str:
        return await self._call_async(
            lambda llm: llm.generate_markdown_diff_summary(
                node_type=node_type,
                change_type=change_type,
                doc1_source_text=doc1_source_text,
                doc2_source_text=doc2_source_text,
                doc1_table_content=doc1_table_content,
                doc2_table_content=doc2_table_content,
                language=language,
            ),
            name="generate_markdown_diff_summary",
        )

    async def aclose(self) -> None:
        await _maybe_await(self.primary.aclose())
        await _maybe_await(self.fallback.aclose())

    def close(self) -> None:
        try:
            self.primary.close()
        finally:
            self.fallback.close()

    async def _call_async(
        self,
        fn: Callable[[BaseLLM], Coroutine[Any, Any, T]],
        *,
        name: str,
    ) -> T:
        try:
            return await fn(self.primary)
        except Exception as exc:
            if not _should_fallback(exc):
                raise
            logger.warning("primary llm %s failed; falling back: %s", name, exc)
            return await fn(self.fallback)

