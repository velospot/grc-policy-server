from __future__ import annotations

from grc_policy_server.core.config import settings
from grc_policy_server.services.llm.base import BaseLLM
from grc_policy_server.services.llm.fallback_llm import FallbackLLM
from grc_policy_server.services.llm.ollama_client import OllamaClient, OllamaSettings
from grc_policy_server.services.llm.vllm_client import VllmClient, VllmSettings


def build_ollama_llm() -> OllamaClient:
    return OllamaClient(
        OllamaSettings(
            base_url=settings.ollama_url,
            chat_model=settings.ollama_chat_model,
            embed_model=settings.ollama_embed_model,
            read_timeout_sec=settings.ollama_timeout_sec,
            opik_enabled=settings.opik_enabled,
            opik_url=settings.opik_url_override,
            opik_project_name=settings.opik_project_name,
            opik_workspace=settings.opik_workspace,
        )
    )


def build_vllm_llm() -> VllmClient:
    return VllmClient(
        VllmSettings(
            chat_url=settings.vllm_chat_url,
            embed_url=settings.vllm_embed_url,
            api_key=settings.vllm_api_key,
            chat_model=settings.vllm_chat_model,
            embed_model=settings.vllm_embed_model,
            connect_timeout_sec=settings.vllm_connect_timeout_sec,
            read_timeout_sec=settings.vllm_timeout_sec,
            max_retries=settings.vllm_max_retries,
            opik_enabled=settings.opik_enabled,
            opik_url=settings.opik_url_override,
            opik_project_name=settings.opik_project_name,
            opik_workspace=settings.opik_workspace,
        )
    )


def build_llm() -> BaseLLM:
    """Build the runtime LLM client based on env config."""
    if not settings.vllm_enabled:
        return build_ollama_llm()

    primary = settings.llm_primary_provider.strip().lower()
    if primary == "ollama":
        return build_ollama_llm()

    # vLLM primary with Ollama fallback.
    return FallbackLLM(primary=build_vllm_llm(), fallback=build_ollama_llm())

