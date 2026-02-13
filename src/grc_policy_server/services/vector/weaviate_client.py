import weaviate


class WeaviateClient:
    def __init__(self):

        self.client = weaviate.connect_to_local("http://weaviate:8080")

    def upsert_chunk(self, chunk: dict, embedding: list[float]):
        self.client.data_object.create(
            data_object=chunk, class_name="PolicyChunk", vector=embedding
        )

    def search(self, embedding: list[float], limit=5):
        return (
            self.client.query.get("PolicyChunk", ["text", "section_path"])
            .with_near_vector({"vector": embedding})
            .with_limit(limit)
            .do()
        )

    def fetch_chunks_by_document(self, document_id: str):
        return (
            self.client.query.get(
                "PolicyChunk",
                ["chunk_id", "text", "section_path", "page", "line_start", "line_end"],
            )
            .with_where(
                {
                    "path": ["document_id"],
                    "operator": "Equal",
                    "valueString": document_id,
                }
            )
            .do()["data"]["Get"]["PolicyChunk"]
        )

    def semantic_search(self, embedding, document_id, limit=1):
        res = (
            self.client.query.get(
                "PolicyChunk",
                ["text", "section_path", "page", "line_start", "line_end"],
            )
            .with_near_vector({"vector": embedding})
            .with_where(
                {
                    "path": ["document_id"],
                    "operator": "Equal",
                    "valueString": document_id,
                }
            )
            .with_limit(limit)
            .do()
        )

        # add similarity explicitly
        for r in res["data"]["Get"]["PolicyChunk"]:
            r["similarity"] = r["_additional"]["certainty"]

        return res["data"]["Get"]["PolicyChunk"]
