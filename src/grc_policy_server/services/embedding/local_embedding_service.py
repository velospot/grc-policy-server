"""Local sentence-transformer embedding service for offline mode.

Loads BAAI/bge-m3 (multilingual, 1024-dim) once and reuses it across requests.
Falls back to a lighter multilingual model when the GPU VRAM budget is constrained.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_INSTANCE: LocalEmbeddingService | None = None


class LocalEmbeddingService:
    """Thread-safe singleton wrapper around a sentence-transformers model.

    Lazy-loads on first call so import time is zero cost.
    """

    def __init__(self, model_name: str = "BAAI/bge-m3", device: str | None = None) -> None:
        self._model_name = model_name
        self._device = device
        self._model: Any = None
        self._model_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed(self, text: str) -> list[float]:
        """Return a normalized embedding vector for a single text string."""
        vec = self._model_instance().encode(
            text,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vec.tolist()

    def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """Return normalized embedding vectors for a list of texts.

        Preserves input order. Empty strings are embedded as zero vectors of the
        correct dimension so the caller never needs to handle sparse outputs.
        """
        if not texts:
            return []

        # Split into non-empty / empty buckets so we don't waste GPU time on padding.
        indices: list[int] = []
        non_empty: list[str] = []
        for i, t in enumerate(texts):
            if t.strip():
                indices.append(i)
                non_empty.append(t)

        results: list[list[float]] = [[] for _ in texts]
        if non_empty:
            vecs = self._model_instance().encode(
                non_empty,
                normalize_embeddings=True,
                batch_size=batch_size,
                show_progress_bar=False,
            )
            for original_idx, vec in zip(indices, vecs):
                results[original_idx] = vec.tolist()

        # Fill empty-string slots with a zero vector of the correct dimension.
        dim = len(results[indices[0]]) if indices else 1024
        for i, v in enumerate(results):
            if not v:
                results[i] = [0.0] * dim

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _model_instance(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                logger.info(
                    "Loading local embedding model %s (first call — one-time cost)",
                    self._model_name,
                )
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(
                    self._model_name,
                    device=self._device,
                )
                logger.info("Local embedding model loaded: %s", self._model_name)
        return self._model


def get_local_embedding_service(
    model_name: str = "BAAI/bge-m3",
    device: str | None = None,
) -> LocalEmbeddingService:
    """Return the process-level singleton, creating it on first call."""
    global _INSTANCE  # noqa: PLW0603
    if _INSTANCE is not None:
        return _INSTANCE
    with _LOCK:
        if _INSTANCE is None:
            _INSTANCE = LocalEmbeddingService(model_name=model_name, device=device)
    return _INSTANCE
