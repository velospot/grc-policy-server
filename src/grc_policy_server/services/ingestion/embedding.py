from __future__ import annotations

from functools import lru_cache
from typing import Iterable

from sentence_transformers import SentenceTransformer

from grc_policy_server.core.config import settings


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    return SentenceTransformer(settings.ollama_embedding_model)


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = get_embedder()
    vectors = model.encode(
        texts,
        batch_size=settings.embed_batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [v.tolist() for v in vectors]
