# src/grc_policy_server/services/llm/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from grc_policy_server.models.schemas import KeyDifference


class BaseLLM(ABC):
    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Return embedding vector for text."""
        raise NotImplementedError

    @abstractmethod
    async def summarize_changes(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
    ) -> str:
        """
        Generate an executive summary of changes based strictly on provided diffs.
        Must not invent changes beyond the diffs passed in.
        """
        raise NotImplementedError

    @abstractmethod
    async def summarize_diff(
        self,
        *,
        old_text: str,
        new_text: str,
        section: str,
    ) -> str:
        """
        Summarize the change for a single chunk pair (for MODIFIED items).
        Must not introduce facts not present in old/new.
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
    ) -> List[str]:
        """
        Generate follow-up questions an auditor should ask, based only on diffs.
        """
        raise NotImplementedError
