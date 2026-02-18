from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import weaviate
from weaviate.classes.config import Configure, DataType, Property
from weaviate.classes.query import Filter
from weaviate.collections.classes.grpc import NearVectorInputType,HybridVectorType
from weaviate.collections.queries import near_vector


@dataclass
class WeaviateConfig:
    collection_name: str = "DocumentChunk"
    enable_bm25: bool = True
    # You embed yourself (nomic-embed-text-v2-moe), so vectors are self-provided.
    self_provided_vectors: bool = True


class WeaviateStore:
    """
    Weaviate v4 store:
      - collection schema created via Property/DataType
      - self-provided vectors for external embeddings
      - optional BM25 inverted index for hybrid search
    """

    def __init__(
        self, client: weaviate.WeaviateClient, cfg: WeaviateConfig | None = None
    ) -> None:
        self.client = client
        self.cfg = cfg or WeaviateConfig()

    # ---------- Schema ----------

    def ensure_schema(self) -> None:
        name = self.cfg.collection_name
        if self.client.collections.exists(name):
            return

        vector_config = Configure.Vectors.self_provided() if self.cfg.self_provided_vectors else None
        # inverted_config = Configure.InvertedIndex.bm25() if self.cfg.enable_bm25 else None

        self.client.collections.create(
            name=name,
            vector_config=vector_config,
            # inverted_index_config=inverted_config,
            properties=[
                Property(name="doc_id", data_type=DataType.TEXT),
                Property(name="chunk_id", data_type=DataType.TEXT),
                Property(name="section_id", data_type=DataType.TEXT),
                Property(name="section_path_titles", data_type=DataType.TEXT_ARRAY),
                Property(name="order_index", data_type=DataType.INT),
                Property(name="page_start", data_type=DataType.INT),
                Property(name="page_end", data_type=DataType.INT),
                Property(name="docling_path", data_type=DataType.TEXT),
                Property(name="main_text", data_type=DataType.TEXT),
                Property(name="has_table", data_type=DataType.BOOL),
                Property(name="has_figure", data_type=DataType.BOOL),
                Property(name="table_ids", data_type=DataType.TEXT_ARRAY),
                Property(name="figure_ids", data_type=DataType.TEXT_ARRAY),
                Property(name="title", data_type=DataType.TEXT),
                Property(name="source_name", data_type=DataType.TEXT),
                Property(name="source_type", data_type=DataType.TEXT),
            ],
        )

    # ---------- Writes ----------

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        """
        chunks: list of dict with required keys:
          - properties in schema
          - "vector": list[float] if self_provided_vectors=True
        """
        self.ensure_schema()
        col = self.client.collections.get(self.cfg.collection_name)

        with col.batch.dynamic() as batch:
            for ch in chunks:
                props = {k: v for k, v in ch.items() if k != "vector"}
                vector = ch.get("vector")

                if self.cfg.self_provided_vectors:
                    if vector is None:
                        raise ValueError(
                            "self_provided_vectors=True requires each chunk to include a 'vector'."
                        )
                    batch.add_object(properties=props, vector=vector)
                else:
                    batch.add_object(properties=props)

    # ---------- Queries ----------

    def query_near_vector(
        self,
        *,
        vector: list[float],
        limit: int = 10,
        doc_id: str | None = None,
        section_id: str | None = None,
        return_properties: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_schema()
        col = self.client.collections.get(self.cfg.collection_name)

        filt = self._build_filter(doc_id=doc_id, section_id=section_id)
        props = list(return_properties) if return_properties else None
        near_vect: NearVectorInputType = vector
        res = col.query.near_vector(
            near_vector=near_vect,
            limit=limit,
            filters=filt,
            return_properties=props,
        )

        # res = col.query.near_vector(
        #     near_vector=vector,
        #     limit=limit,
        #     filters=filt,
        #     return_properties=props,
        # )
        return [self._obj_to_dict(o) for o in res.objects]

    def bm25(
        self,
        *,
        query: str,
        limit: int = 10,
        doc_id: str | None = None,
        section_id: str | None = None,
        return_properties: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Keyword search (requires enable_bm25=True in schema).
        """
        if not self.cfg.enable_bm25:
            raise RuntimeError(
                "BM25 disabled. Create collection with enable_bm25=True."
            )
        self.ensure_schema()
        col = self.client.collections.get(self.cfg.collection_name)

        filt = self._build_filter(doc_id=doc_id, section_id=section_id)
        props = list(return_properties) if return_properties else None

        res = col.query.bm25(
            query=query,
            limit=limit,
            filters=filt,
            return_properties=props,
        )
        return [self._obj_to_dict(o) for o in res.objects]

    def hybrid(
        self,
        *,
        query: str,
        vector: list[float] | None = None,
        alpha: float = 0.7,
        limit: int = 10,
        doc_id: str | None = None,
        section_id: str | None = None,
        return_properties: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Hybrid = vector + BM25
        alpha: 0..1 ; higher means more vector influence.
        If you embed externally, pass `vector`.
        """
        if not self.cfg.enable_bm25:
            raise RuntimeError("Hybrid requires BM25 enabled (enable_bm25=True).")

        self.ensure_schema()
        col = self.client.collections.get(self.cfg.collection_name)

        filt = self._build_filter(doc_id=doc_id, section_id=section_id)
        props = list(return_properties) if return_properties else None


        # v4: hybrid supports query + alpha; and can take near_vector to combine.
        res = col.query.hybrid(
            query=query,
            alpha=alpha,
            vector=vector,
            limit=limit,
            filters=filt,
            return_properties=props
        )

        return [self._obj_to_dict(o) for o in res.objects]

    # ---------- Internals ----------

    def _build_filter(
        self, *, doc_id: str | None, section_id: str | None)

        f = None

        if doc_id:
            f = Filter.by_property("doc_id").equal(doc_id)

        if section_id:
            f2 = Filter.by_property("section_id").equal(section_id)
            f = f2 if f is None else f & f2

        return f

    @staticmethod
    def _obj_to_dict(o) -> dict[str, Any]:
        d = dict(o.properties)
        # optional: include distance/score if present
        if getattr(o, "metadata", None):
            md = o.metadata
            if hasattr(md, "distance") and md.distance is not None:
                d["_distance"] = md.distance
            if hasattr(md, "score") and md.score is not None:
                d["_score"] = md.score
        return d
