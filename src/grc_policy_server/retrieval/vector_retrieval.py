from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grc_policy_server.services.ingestion.embedding import embed_texts
from grc_policy_server.storage.weaviate_store import WeaviateStore


@dataclass
class RetrievalConfig:
    limit: int = 8
    alpha: float = 0.7  # vector weight
    return_props: tuple[str, ...] = (
        "doc_id",
        "chunk_id",
        "section_id",
        "section_path_titles",
        "order_index",
        "page_start",
        "page_end",
        "docling_path",
        "main_text",
        "title",
        "source_name",
        "source_type",
        "has_table",
        "has_figure",
        "table_ids",
        "figure_ids",
    )


def hybrid_retrieve(
    weaviate_store: WeaviateStore,
    *,
    query: str,
    doc_id: str | None = None,
    section_id: str | None = None,
    cfg: RetrievalConfig | None = None,
) -> list[dict[str, Any]]:
    cfg = cfg or RetrievalConfig()
    qvec = embed_texts([query])[0]

    # Hybrid: keyword + vector
    hits = weaviate_store.hybrid(
        query=query,
        vector=qvec,
        alpha=cfg.alpha,
        limit=cfg.limit,
        doc_id=doc_id,
        section_id=section_id,
        return_properties=cfg.return_props,
    )
    return hits
