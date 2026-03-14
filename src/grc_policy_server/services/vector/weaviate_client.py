from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

import weaviate
from weaviate.classes.config import Configure, DataType, Property
from weaviate.classes.query import Filter, MetadataQuery, Sort
from weaviate.connect.base import ConnectionParams

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
        self.collection_name = settings.weaviate_collection
        self.client = self._build_client()
        self._schema_ensured = False

    def _build_client(self):
        if settings.weaviate_embedded:
            return weaviate.connect_to_embedded()

        parsed = urlparse(settings.weaviate_url)
        if not parsed.scheme or not parsed.hostname:
            parsed = urlparse("http://localhost:8080")

        http_secure = parsed.scheme == "https"
        grpc_host = settings.weaviate_grpc_host or parsed.hostname or ""
        grpc_secure = (
            settings.weaviate_grpc_secure
            if settings.weaviate_grpc_secure is not None
            else http_secure
        )
        http_port = parsed.port or (443 if http_secure else 8080)
        grpc_port = settings.weaviate_grpc_port or (443 if grpc_secure else 50051)
        auth = (
            weaviate.auth.AuthApiKey(settings.weaviate_api_key)
            if settings.weaviate_api_key
            else None
        )

        return weaviate.WeaviateClient(
            connection_params=ConnectionParams.from_params(
                http_host=parsed.hostname or "localhost:8080",
                http_port=http_port,
                http_secure=http_secure,
                grpc_host=grpc_host,
                grpc_port=grpc_port,
                grpc_secure=grpc_secure,
            ),
            auth_client_secret=auth,
            skip_init_checks=True,
        )

    def close(self) -> None:
        self.client.close()

    def _ensure_ready(self) -> None:
        self.client.connect()
        if not self._schema_ensured:
            self._ensure_schema()
            self._schema_ensured = True

    def _schema_properties(self) -> list[Property]:
        return [
            Property(name="chunk_id", data_type=DataType.TEXT),
            Property(name="document_id", data_type=DataType.TEXT),
            Property(name="document_stable_id", data_type=DataType.TEXT),
            Property(name="stable_id", data_type=DataType.TEXT),
            Property(name="content_hash", data_type=DataType.TEXT),
            Property(name="node_type", data_type=DataType.TEXT),
            Property(name="parent_id", data_type=DataType.TEXT),
            Property(name="title", data_type=DataType.TEXT),
            Property(name="section_path", data_type=DataType.TEXT),
            Property(name="text", data_type=DataType.TEXT),
            Property(name="clean_text", data_type=DataType.TEXT),
            Property(name="chunk_index", data_type=DataType.INT),
            Property(name="page_number", data_type=DataType.INT),
            Property(name="indexable", data_type=DataType.BOOL),
            Property(name="excluded_from_index", data_type=DataType.BOOL),
            Property(name="exclusion_reason", data_type=DataType.TEXT),
            Property(name="lineage", data_type=DataType.TEXT_ARRAY),
            Property(name="lineage_ids", data_type=DataType.TEXT_ARRAY),
            Property(name="source", data_type=DataType.TEXT),
            Property(name="obligation", data_type=DataType.TEXT),
            Property(name="subject", data_type=DataType.TEXT),
            Property(name="action", data_type=DataType.TEXT),
            Property(name="object", data_type=DataType.TEXT),
            Property(name="condition", data_type=DataType.TEXT),
        ]

    def _ensure_schema(self) -> None:
        name = self.collection_name
        if not self.client.collections.exists(name):
            self.client.collections.create(
                name=name,
                properties=self._schema_properties(),
                vector_config=Configure.Vectors.text2vec_ollama(
                    api_endpoint="http://host.docker.internal:11434",
                    model=settings.ollama_embed_model,
                    source_properties=["clean_text", "text", "section_path", "title"],
                ),
            )
            return

        existing = {
            prop.name
            for prop in self.client.collections.get(name)
            .config.get(simple=True)
            .properties
        }
        for prop in self._schema_properties():
            if prop.name in existing:
                continue
            try:
                self.client.collections.get(name).config.add_property(prop)
            except Exception:
                logger.exception(
                    "failed to add weaviate property=%s collection=%s",
                    prop.name,
                    name,
                )

    @property
    def collection(self):
        self._ensure_ready()
        return self.client.collections.get(self.collection_name)

    def delete_chunks_by_document(self, document_id: str) -> int:
        result = self.collection.data.delete_many(
            where=Filter.by_property("document_id").equal(document_id)
        )
        return int(result.successful)

    def fetch_chunks_by_document(self, document_id: str) -> List[Dict[str, Any]]:
        resp = self.collection.query.fetch_objects(
            filters=Filter.by_property("document_id").equal(document_id),
            limit=10000,
            sort=Sort.by_property(name="page_number", ascending=True),
            return_properties=self._return_properties(),
        )
        resp.objects
        return [dict(obj.properties) for obj in resp.objects]

    def upsert_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        with self.collection.batch.dynamic() as batch:
            for chunk in chunks:
                chunk_id = str(chunk["chunk_id"])
                props = {
                    "chunk_id": chunk_id,
                    "document_id": str(chunk["document_id"]),
                    "document_stable_id": str(chunk.get("document_stable_id") or ""),
                    "stable_id": str(chunk.get("stable_id") or ""),
                    "content_hash": str(chunk.get("content_hash") or ""),
                    "node_type": str(chunk.get("node_type") or "clause"),
                    "parent_id": str(chunk.get("parent_id") or ""),
                    "title": str(chunk.get("title") or ""),
                    "section_path": str(chunk.get("section_path") or "Unknown Section"),
                    "text": str(chunk.get("text") or ""),
                    "clean_text": str(chunk.get("clean_text") or ""),
                    "chunk_index": int(chunk.get("chunk_index") or 0),
                    "page_number": chunk.get("page_number"),
                    "indexable": bool(chunk.get("indexable", True)),
                    "excluded_from_index": bool(
                        chunk.get("excluded_from_index", False)
                    ),
                    "exclusion_reason": str(chunk.get("exclusion_reason") or ""),
                    "lineage": list(chunk.get("lineage") or []),
                    "lineage_ids": list(chunk.get("lineage_ids") or []),
                    "source": str(chunk.get("source") or "docling"),
                    "obligation": str(chunk.get("obligation") or ""),
                    "subject": str(chunk.get("subject") or ""),
                    "action": str(chunk.get("action") or ""),
                    "object": str(chunk.get("object") or ""),
                    "condition": str(chunk.get("condition") or ""),
                }
                unique_id = str(uuid5(NAMESPACE_URL, chunk_id))
                batch.add_object(
                    properties=props,
                    uuid=unique_id,
                )

    def semantic_search_in_document(
        self,
        *,
        query_vector: List[float],
        target_document_id: str,
        limit: int = 3,
        node_types: list[str] | None = None,
    ) -> List[Dict[str, Any]]:
        resp = self.collection.query.near_vector(
            near_vector=query_vector,
            limit=limit,
            filters=self._document_filter(target_document_id, node_types=node_types),
            return_properties=self._return_properties(),
            return_metadata=["distance"],
        )

        out: List[Dict[str, Any]] = []
        for obj in resp.objects:
            item = dict(obj.properties)
            item["_distance"] = obj.metadata.distance
            item["_score"] = getattr(obj.metadata, "score", None)
            out.append(item)
        return out

    def hybrid_search_in_document(
        self,
        *,
        query_string: str,
        target_document_id: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        resp = self.collection.query.hybrid(
            query=query_string,
            limit=limit,
            filters=Filter.by_property("document_id").equal(target_document_id),
            return_properties=self._return_properties(),
            return_metadata=MetadataQuery(score=True, distance=True),
        )

        out: List[Dict[str, Any]] = []
        for obj in resp.objects:
            item = dict(obj.properties)
            item["_distance"] = obj.metadata.distance
            item["_score"] = obj.metadata.score
            out.append(item)
        return out

    def search_section_in_document(
        self,
        *,
        query_string: str,
        query_text: str,
        target_document_id: str,
        limit: int = 3,
        node_types: list[str] | None = None,
    ) -> List[Dict[str, Any]]:
        resp = self.collection.query.near_text(
            filters=self._document_filter(target_document_id, node_types=node_types),
            query=query_text or query_string,
            limit=limit,
            return_properties=self._return_properties(),
            return_metadata=MetadataQuery(score=True, distance=True),
        )

        out: List[Dict[str, Any]] = []
        for obj in resp.objects:
            item = dict(obj.properties)
            item["_distance"] = obj.metadata.distance
            item["_score"] = obj.metadata.score
            out.append(item)
        return out

    def _document_filter(
        self,
        document_id: str,
        *,
        node_types: list[str] | None = None,
    ):
        filters = [Filter.by_property("document_id").equal(document_id)]
        if node_types:
            filters.append(
                Filter.any_of(
                    [
                        Filter.by_property("node_type").equal(node_type)
                        for node_type in node_types
                    ]
                )
            )
        return Filter.all_of(filters)

    def _return_properties(self) -> list[str]:
        return [
            "chunk_id",
            "document_id",
            "document_stable_id",
            "stable_id",
            "content_hash",
            "node_type",
            "parent_id",
            "title",
            "section_path",
            "text",
            "clean_text",
            "chunk_index",
            "page_number",
            "indexable",
            "excluded_from_index",
            "exclusion_reason",
            "lineage",
            "lineage_ids",
            "source",
            "obligation",
            "subject",
            "action",
            "object",
            "condition",
        ]