from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Optional

import httpx

from grc_policy_server.core.logging import logging
from grc_policy_server.services.llm.ollama_client import OllamaClient
from grc_policy_server.services.observability import tracing

logger = logging.getLogger(__name__)


def _drain_think_buffer(buf: str, in_think: bool) -> tuple[str, bool, str]:
    """Parse `<think>...</think>` tags out of a streaming token buffer.

    Returns (remaining_buf, in_think, text_to_emit).
    Text inside <think> tags is discarded; everything outside is emitted.
    """
    emit = []
    while buf:
        if in_think:
            end = buf.find("</think>")
            if end == -1:
                # Still inside <think>, hold the whole buffer
                return buf, True, "".join(emit)
            # Found closing tag — skip everything up to and including it
            buf = buf[end + len("</think>"):]
            in_think = False
        else:
            start = buf.find("<think>")
            if start == -1:
                emit.append(buf)
                buf = ""
            else:
                emit.append(buf[:start])
                buf = buf[start + len("<think>"):]
                in_think = True
    return "", in_think, "".join(emit)


@dataclass(frozen=True)
class VllmSettings:
    """Settings for an OpenAI-compatible vLLM server.

    ``chat_url`` and ``embed_url`` are set independently so that chat and
    embedding inference can be served from different endpoints (e.g. separate
    vLLM processes, ports, or hosts).
    """

    chat_url: str = "http://localhost:8001"
    embed_url: str = "http://localhost:8001"
    api_key: str | None = None
    chat_model: str = "ibm-granite/granite-3.3-8b-instruct"
    embed_model: str = "Qwen/Qwen3-Embedding-0.6B"
    connect_timeout_sec: float = 5.0
    read_timeout_sec: float = 600.0
    write_timeout_sec: float = 60.0
    max_retries: int = 2
    opik_enabled: bool = False
    opik_url: str = "http://localhost:5173/api"
    opik_project_name: str = "grc-policy-server"
    opik_workspace: str = "default"


class VllmClient(OllamaClient):
    """vLLM-backed implementation using OpenAI-compatible endpoints.

    This subclasses `OllamaClient` to reuse the prompt + parsing logic for the
    policy workflows, while swapping out the HTTP transport to vLLM.

    Two separate httpx clients are used so that chat and embedding requests each
    target their own base URL (and thus their own connection pool) without any
    URL-merging ambiguity:
      - ``_async_client``  → ``chat_url``   (used by ``_post_json``)
      - ``_sync_client``   → ``embed_url``  (used by ``_post_json_sync``)
    """

    def __init__(self, settings: Optional[VllmSettings] = None):
        # NOTE: this intentionally does not call OllamaClient.__init__.
        self.settings = settings or VllmSettings()
        self._meaning_cache: dict[str, dict[str, str]] = {}

        tracing.configure(
            enabled=self.settings.opik_enabled,
            url=self.settings.opik_url,
            project_name=self.settings.opik_project_name,
            workspace=self.settings.opik_workspace,
        )

        headers: dict[str, str] = {}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        _timeout = httpx.Timeout(
            connect=self.settings.connect_timeout_sec,
            read=self.settings.read_timeout_sec,
            write=self.settings.write_timeout_sec,
            pool=self.settings.connect_timeout_sec,
        )

        # Separate async clients so chat and embedding can target different endpoints.
        self._async_client = httpx.AsyncClient(
            base_url=self.settings.chat_url,
            headers=headers,
            timeout=_timeout,
        )
        self._async_embed_client = httpx.AsyncClient(
            base_url=self.settings.embed_url,
            headers=headers,
            timeout=_timeout,
        )

    # ------------------------------------------------------------------
    # Transport helpers
    # ------------------------------------------------------------------

    async def _post_json_embed(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            if attempt > 0:
                backoff = min(2.0 ** attempt, 30.0)
                await asyncio.sleep(random.uniform(backoff * 0.75, backoff * 1.25))
            try:
                r = await self._async_embed_client.post(path, json=payload)
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict):
                    raise RuntimeError(f"vLLM response is not JSON object: {data}")
                return data
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning(
                    "vLLM embed timeout on attempt %d/%d: %s",
                    attempt + 1,
                    self.settings.max_retries + 1,
                    path,
                )
        raise RuntimeError(
            f"vLLM request timed out after {self.settings.max_retries + 1} attempts"
        ) from last_exc

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
                    raise RuntimeError(f"vLLM response is not JSON object: {data}")
                return data
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning(
                    "vLLM async timeout on attempt %d/%d: %s",
                    attempt + 1,
                    self.settings.max_retries + 1,
                    path,
                )
        raise RuntimeError(
            f"vLLM request timed out after {self.settings.max_retries + 1} attempts"
        ) from last_exc

    # ------------------------------------------------------------------
    # OpenAI-compatible endpoints
    # ------------------------------------------------------------------

    async def embed(self, text: str) -> list[float]:
        tracked = tracing.track(
            name="vllm_embed",
            type="llm",
            tags=["llm", "vllm", "embedding"],
            metadata={"model": self.settings.embed_model},
            project_name=self.settings.opik_project_name,
        )(self._embed_untraced)
        return await tracked(text)

    async def _embed_untraced(self, text: str) -> list[float]:
        payload = {
            "model": self.settings.embed_model,
            "input": text,
        }
        response = await self._post_json_embed("/v1/embeddings", payload)
        data = response.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                embedding = first.get("embedding")
                if isinstance(embedding, list):
                    return [float(value) for value in embedding]
        raise RuntimeError(f"Unexpected vLLM embeddings response: {response}")

    def close(self) -> None:
        super().close()
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_embed_client.aclose())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._async_embed_client.aclose())
            finally:
                loop.close()

    async def aclose(self) -> None:
        await self._async_client.aclose()
        await self._async_embed_client.aclose()

    async def _generate_text(self, prompt: str, temperature=None) -> str:  # type: ignore[override]
        temp = 0 if temperature is None else temperature
        tracked = tracing.track(
            name="vllm_generate",
            type="llm",
            tags=["llm", "vllm", "generation"],
            metadata={"model": self.settings.chat_model, "temperature": temp},
            project_name=self.settings.opik_project_name,
        )(self._generate_text_untraced)
        return await tracked(prompt, temp)

    async def _generate_text_untraced(self, prompt: str, temperature: float) -> str:  # type: ignore[override]
        response = await self._post_json(
            "/v1/chat/completions",
            {
                "model": self.settings.chat_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": float(temperature),
            },
        )
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message") or {}
                if isinstance(message, dict):
                    content = str(message.get("content") or "").strip()
                    content = re.sub(
                        r"<think>.*?</think>", "", content, flags=re.DOTALL
                    ).strip()
                    return content
        raise RuntimeError(f"Unexpected vLLM chat response: {response}")

    async def _post_json_stream(self, path: str, payload: dict) -> AsyncIterator[str]:
        """POST with SSE streaming; yield raw content delta tokens."""
        async with self._async_client.stream("POST", path, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        token = data["choices"][0]["delta"].get("content", "")
                        if token:
                            yield token
                    except Exception:
                        continue

    async def _generate_text_stream(self, prompt: str, temperature: float) -> AsyncIterator[str]:
        """Stream tokens from /v1/chat/completions, stripping <think> tags."""
        payload = {
            "model": self.settings.chat_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(temperature),
            "stream": True,
        }
        in_think = False
        buf = ""
        async for token in self._post_json_stream("/v1/chat/completions", payload):
            buf += token
            buf, in_think, emit = _drain_think_buffer(buf, in_think)
            if emit:
                yield emit
        # Flush any remaining buffer after stream ends
        if buf and not in_think:
            yield buf

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
        """Yield LLM tokens for a markdown diff summary using SSE streaming."""
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
        async for token in self._generate_text_stream(prompt, 0.0):
            yield token

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
        prompt = self._prompt_change_record_json(
            change_id=change_id,
            node_type=node_type,
            change_type=change_type,
            doc1_source_text=doc1_source_text,
            doc2_source_text=doc2_source_text,
            language=language,
            testing_department=testing_department,
        )
        async for token in self._generate_text_stream(prompt, 0.0):
            yield token
