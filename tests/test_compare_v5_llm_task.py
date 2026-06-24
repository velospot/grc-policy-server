from __future__ import annotations

from grc_policy_server.core.config import settings
from grc_policy_server.tasks import compare_v2 as compare_task


class _FakeQdrantClient:
    def get_collections(self) -> list:
        return []


class _FakeQdrant:
    def __init__(self) -> None:
        self._client = _FakeQdrantClient()

    def close(self) -> None:
        return


class _FakeLLM:
    pass


def test_v5_compare_task_uses_configured_llm(monkeypatch):
    llm = _FakeLLM()
    monkeypatch.setattr(compare_task, "QdrantVectorClient", _FakeQdrant)
    monkeypatch.setattr(compare_task, "build_llm", lambda: llm)
    monkeypatch.setattr(settings, "neo4j_enabled", False)

    engine, qdrant, neo4j, built_llm = compare_task._build_diff_engine(api_version="v5")

    assert built_llm is llm
    assert engine.llm is llm
    assert engine.max_diffs == 1000
    assert engine.max_llm_explanations == 40
    assert engine.max_llm_markdown_summaries == 40
    assert qdrant is not None
    assert neo4j is None
