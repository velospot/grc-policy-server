from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import weaviate
from weaviate.classes.config import Configure, DataType, Property
from weaviate.classes.query import Filter

from grc_policy_server.core.config import settings


@dataclass(frozen=True)
class WeaviateSettings:
    http_host: str = "weaviate"
    http_port: int = 8080
    grpc_host: str = "weaviate"
    grpc_port: int = 50051
    collection_name: str = "PolicyChunk"


class WeaviateClient:
    def __init__(self):
        if settings.weaviate_embedded:
            self.client = weaviate.connect_to_embedded()
        else:
            try:
                self.client = weaviate.connect_to_local(settings.weaviate_url)
            except Exception:
                self.client = weaviate.connect_to_local()
        self.collection_name = settings.weaviate_collection
        self._ensure_schema()
        self._ensure_collection()

    def _ensure_collection(self):
        # Docling RAG example pattern: pre-create/configure collection.
        try:
            from weaviate.classes.config import Configure, DataType, Property

            if self.client.collections.exists(self.collection_name):
                return

            self.client.collections.create(
                name=self.collection_name,
                vectorizer_config=Configure.Vectorizer.none(),
                properties=[
                    Property(name="chunk_id", data_type=DataType.TEXT),
                    Property(name="document_id", data_type=DataType.TEXT),
                    Property(name="text", data_type=DataType.TEXT),
                    Property(name="chunk_type", data_type=DataType.TEXT),
                    Property(name="section_path", data_type=DataType.TEXT),
                    Property(name="filename", data_type=DataType.TEXT),
                    Property(name="page_number", data_type=DataType.INT),
                    Property(name="level", data_type=DataType.INT),
                ],
            )
        except Exception:
            # Keep backward compatibility with older client/server setups.
            pass

    def close(self) -> None:
        self.client.close()

    def _ensure_schema(self) -> None:
        name = self.collection_name
        if self.client.collections.exists(name):
            return

        self.client.collections.create(
            name=name,
            vectorizer_config=Configure.Vectorizer.none(),
            properties=[
                Property(name="chunk_id", data_type=DataType.TEXT),
                Property(name="document_id", data_type=DataType.TEXT),
                Property(name="section_path", data_type=DataType.TEXT),
                Property(name="text", data_type=DataType.TEXT),
                Property(name="chunk_index", data_type=DataType.INT),
            ],
        )

    @property
    def collection(self):
        return self.client.collections.get(self.collection_name)

    def fetch_chunks_by_document(self, document_id: str) -> List[Dict[str, Any]]:
        resp = self.collection.query.fetch_objects(
            filters=Filter.by_property("document_id").equal(document_id),
            limit=10000,
            return_properties=[
                "chunk_id",
                "document_id",
                "section_path",
                "text",
                "chunk_index",
            ],
        )

        return [dict(o.properties) for o in resp.objects]

    def semantic_search_in_document(
        self,
        *,
        query_vector: List[float],
        target_document_id: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        from weaviate.classes.query import Filter

        resp = self.collection.query.near_vector(
            near_vector=query_vector,
            limit=limit,
            filters=Filter.by_property("document_id").equal(target_document_id),
            return_properties=[
                "chunk_id",
                "document_id",
                "section_path",
                "text",
                "chunk_index",
            ],
            return_metadata=["distance"],
        )

        out: List[Dict[str, Any]] = []
        for obj in resp.objects:
            d = dict(obj.properties)
            d["_distance"] = obj.metadata.distance
            out.append(d)
        return out
