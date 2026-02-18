from __future__ import annotations

from dataclasses import dataclass

from grc_policy_server.models.ingestion_model import DocumentChunk, DocumentResult
from neo4j import GraphDatabase


@dataclass
class Neo4jStore:
    uri: str = "bolt://neo4j:7687"
    user: str = "neo4j"
    password: str = "password"
    database: str = "neo4j"

    def __post_init__(self) -> None:
        self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def close(self) -> None:
        self._driver.close()

    def upsert_document_with_chunks(self, result: DocumentResult) -> None:
        with self._driver.session() as session:
            session.execute_write(self._write_document, result)

    @staticmethod
    def _write_document(tx, result: DocumentResult) -> None:
        tx.run(
            """
            MERGE (d:Document {document_id: $document_id})
            SET d.title = $title
            """,
            document_id=result.document_id,
            title=result.docling_json.get("metadata", {}).get("title"),
        )

        for ch in result.chunks:
            Neo4jStore._write_chunk(tx, result.document_id, ch)

    @staticmethod
    def _write_chunk(tx, document_id: str, ch: DocumentChunk) -> None:
        tx.run(
            """
            MATCH (d:Document {document_id: $document_id})
            MERGE (c:Chunk {chunk_id: $chunk_id})
            SET c.page_number = $page_number,
                c.docling_path = $docling_path,
                c.source_type = $source_type,
                c.source_name = $source_name,
                c.main_text = $main_text,
                c.section_path = $section_path
            MERGE (d)-[:HAS_CHUNK]->(c)
            """,
            document_id=document_id,
            chunk_id=ch.chunk_id,
            page_number=ch.metadata.page_number,
            docling_path=ch.metadata.docling_path,
            source_type=ch.metadata.source_type,
            source_name=ch.metadata.source_name,
            main_text=ch.main_text,
            section_path=ch.metadata.section_path,
        )
