from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grc_policy_server.retrieval.graph_retrieval import (
    Neo4jContextExpander,
    Neo4jExpandConfig,
)
from grc_policy_server.retrieval.vector_retrieval import (
    RetrievalConfig,
    hybrid_retrieve,
)
from grc_policy_server.storage.weaviate_store import WeaviateStore


@dataclass
class RagOrchestratorConfig:
    retrieval: RetrievalConfig = RetrievalConfig()
    expand: Neo4jExpandConfig = Neo4jExpandConfig()


def retrieve_and_expand(
    weaviate_store: WeaviateStore,
    neo4j_expander: Neo4jContextExpander,
    *,
    query: str,
    doc_id: str | None = None,
    section_id: str | None = None,
    cfg: RagOrchestratorConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or RagOrchestratorConfig()

    hits = hybrid_retrieve(
        weaviate_store,
        query=query,
        doc_id=doc_id,
        section_id=section_id,
        cfg=cfg.retrieval,
    )

    expanded = neo4j_expander.expand_hits(hits, cfg.expand)

    # Build a clean, LLM-ready context bundle
    context_blocks = []
    for ch in expanded:
        header = f"[doc={ch['doc_id']} sec={ch['section_id']} order={ch['order_index']} pages={ch.get('page_start')}]"
        block = header + "\n" + (ch.get("main_text") or "")
        context_blocks.append(block)

    return {
        "query": query,
        "hits": hits,  # raw semantic hits (for debug/UX)
        "expanded_chunks": expanded,  # structure-expanded chunks
        "context_text": "\n\n---\n\n".join(context_blocks),
    }
