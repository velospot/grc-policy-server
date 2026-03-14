# src/grc_policy_server/services/llm/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List

from grc_policy_server.models.schemas import KeyDifference


class BaseLLM(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return embedding vector for text."""
        raise NotImplementedError

    @abstractmethod
    async def extract_policy_meanings(
        self,
        *,
        texts: List[str],
        language: str = "",
    ) -> List[Dict[str, str]]:
        """
        Extract normalized clause meaning for policy statements in any language.
        The returned list must preserve input order.
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
    async def generate_followups(
        self,
        *,
        doc1_name: str,
        doc2_name: str,
        key_differences: List[KeyDifference],
        max_questions: int = 6,
        language: str = "",
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
