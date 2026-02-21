from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
from uuid import NAMESPACE_URL, uuid5

import weaviate
from weaviate.classes.config import Configure, DataType, Property
from weaviate.classes.query import Filter, MetadataQuery

from grc_policy_server.core.config import settings
from grc_policy_server.core.logging import logging

logger = logging.getLogger(__name__)


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
        # self._ensure_collection()

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
            properties=[
                Property(name="chunk_id", data_type=DataType.TEXT),
                Property(name="document_id", data_type=DataType.TEXT),
                Property(name="section_path", data_type=DataType.TEXT),
                Property(name="text", data_type=DataType.TEXT),
                Property(name="chunk_index", data_type=DataType.INT),
                Property(name="page_number", data_type=DataType.INT),
            ],
            vector_config=Configure.Vectors.text2vec_ollama(
                api_endpoint=settings.ollama_url,
                model=settings.ollama_embed_model,
                source_properties=["text", "section_path"],
            ),
        )

    @property
    def collection(self):
        return self.client.collections.get(self.collection_name)

    def fetch_chunks_by_document(self, document_id: str) -> List[Dict[str, Any]]:
        # filter = Filter.all_of(
        #      # Combines the below with `|`
        #         Filter.by_property("document_id").equal(document_id)
        #         Filter.not_(Filter.by_property("section_path").equal("")),

        # )

        resp = self.collection.query.fetch_objects(
            filters=Filter.by_property("document_id").equal(document_id),
            limit=10000,
            return_properties=[
                "chunk_id",
                "document_id",
                "section_path",
                "text",
                "chunk_index",
                "page_number",
            ],
        )
        # logger.info("doc_id", document_id, "resp", resp.objects)
        out: List[Dict[str, Any]] = []
        for obj in resp.objects:
            item = dict(obj.properties)
            # if "page" not in item and "page_number" in item:
            #     item["page"] = item.get("page_number")
            out.append(item)
        return out

    def upsert_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        with self.collection.batch.dynamic() as batch:
            # chunks_Ids = []
            for chunk in chunks:
                chunk_id = str(chunk["chunk_id"])

                props = {
                    "chunk_id": chunk_id,
                    "document_id": str(chunk["document_id"]),
                    "section_path": str(chunk.get("section_path") or "Unknown Section"),
                    "text": str(chunk.get("text") or ""),
                    "chunk_index": int(chunk.get("chunk_index") or 0),
                    "page_number": chunk.get("page_number"),
                }
                unique_id = str(uuid5(NAMESPACE_URL, chunk_id))
                batch.add_object(
                    properties=props,
                    uuid=unique_id,
                )
                # chunks_Ids.append(uniqueId)

            # logger.info("chunkids= %s", chunks_Ids)

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
            text_obj = str(d["text"]).strip()
            section_obj = str(d["section_path"]).strip()
            if "..." not in text_obj or "..." not in section_obj:
                out.append(d)
        return out

    def hybrid_search_in_document(
        self,
        *,
        query_string: str,
        target_document_id: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        from weaviate.classes.query import Filter

        resp = self.collection.query.hybrid(
            query=query_string,
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
        # logger.info("found= %s", resp.objects)
        for obj in resp.objects:
            d = dict(obj.properties)
            d["_distance"] = obj.metadata.distance
            out.append(d)
        return out

    def search_section_in_document(
        self,
        *,
        query_string: str,
        query_text: str,
        target_document_id: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        from weaviate.classes.query import Filter

        _filts = Filter.by_property("document_id").equal(target_document_id)
        resp = self.collection.query.near_text(
            filters=_filts,
            query=query_text,
            limit=limit,
            return_metadata=MetadataQuery(score=True, distance=True),
        )

        out: List[Dict[str, Any]] = []
        # logger.info("found= %s", resp.objects)
        for obj in resp.objects:
            d = dict(obj.properties)
            d["_distance"] = obj.metadata.distance
            out.append(d)

        return out
