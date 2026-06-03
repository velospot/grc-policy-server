"""LLM client factory — Ollama only.

The sole supported backend is OllamaClient which uses the local Ollama instance.
All comparisons, explanations, summaries, and embeddings route through Ollama.

Offline degradation (COMPARISON_BACKEND=offline) injects NoOpLLM via the
OfflineDiffEngine path; the factory is not called in that path.
"""
from __future__ import annotations

from grc_policy_server.core.config import settings
from grc_policy_server.services.llm.base import BaseLLM
from grc_policy_server.services.llm.ollama_client import OllamaClient, OllamaSettings


def build_llm() -> BaseLLM:
    """Build and return the Ollama LLM client."""
    return OllamaClient(
        OllamaSettings(
            base_url=settings.ollama_url,
            chat_model=settings.ollama_chat_model,
            embed_model=settings.ollama_embed_model,
            connect_timeout_sec=settings.ollama_connect_timeout_sec,
            read_timeout_sec=settings.ollama_timeout_sec,
            write_timeout_sec=settings.ollama_write_timeout_sec,
            opik_enabled=settings.opik_enabled,
            opik_url=settings.opik_url_override,
            opik_project_name=settings.opik_project_name,
            opik_workspace=settings.opik_workspace,
        )
    )
