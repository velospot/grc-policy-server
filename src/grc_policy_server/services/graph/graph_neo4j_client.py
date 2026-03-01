from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from neo4j import GraphDatabase


@dataclass(frozen=True)
class Neo4jSettings:
    # uri: str = "bolt://neo4j:7687"
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = "your_password"
    database: str = "neo4j"


class Neo4jClient:
    def __init__(self, settings: Neo4jSettings):
        self.settings = settings
        print("Connecting to:", settings.uri)  # 👈 ADD THIS
        self._driver = GraphDatabase.driver(
            settings.uri, auth=(settings.user, settings.password)
        )

    def close(self) -> None:
        self._driver.close()

    def upsert_document_with_chunks(
        self,
        *,
        document_id: str,
        filename: str,
        chunks: list[dict[str, Any]],
    ) -> None:
        self._driver.execute_query(
            """
            MERGE (d:Document {id: $document_id})
            SET d.document_id = $document_id,
                d.name = $filename,
                d.updated_at = datetime()
            WITH d
            UNWIND $chunks AS ch
            MERGE (c:Chunk {id: ch.chunk_id})
            SET c.chunk_id = ch.chunk_id,
                c.document_id = $document_id,
                c.text = coalesce(ch.text, ""),
                c.source_text = coalesce(ch.text, ""),
                c.section_path = coalesce(ch.section_path, "Unknown Section"),
                c.page = coalesce(ch.page_number, 0),
                c.line_start = ch.line_start,
                c.line_end = ch.line_end,
                c.chunk_index = coalesce(ch.chunk_index, 0),
                c.docling_path = ch.docling_path
            MERGE (d)-[:HAS_CHUNK]->(c)
            MERGE (s:Section {path: coalesce(ch.section_path, "Unknown Section")})
            SET s.full_path = coalesce(ch.section_path, "Unknown Section")
            MERGE (s)-[:HAS_CHUNK]->(c)
            """,
            document_id=document_id,
            filename=filename,
            chunks=chunks,
            database_=self.settings.database,
        )

    def resolve_section_path(self, chunk_id: str) -> str:
        with self._driver.session(database=self.settings.database) as session:
            res = session.run(
                """
            MATCH (s:Section)-[:HAS_CHUNK]->(c:Chunk {id: $chunk_id})
            RETURN s.full_path AS path
            """,
                chunk_id=chunk_id,
            ).single()

            return res["path"] if res else "Unknown Section"

    def get_chunk_citation(self, *, chunk_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns a citation payload derived from Neo4j graph data.
        This is the canonical source for references shown in UI.
        """
        recs, _, _ = self._driver.execute_query(
            """
            MATCH (c:Chunk {id: $chunk_id})
            OPTIONAL MATCH (s:Section)-[:HAS_CHUNK]->(c)
            RETURN
              coalesce(s.path, c.section_path, "Unknown Section") AS section_path,
              coalesce(c.page, 0) AS page,
              c.line_start AS line_start,
              c.line_end AS line_end,
              coalesce(c.source_text, "") AS source_text
            LIMIT 1
            """,
            chunk_id=chunk_id,
            database_=self.settings.database,
        )

        if not recs:
            return None

        r = recs[0]
        return {
            "section": r["section_path"],
            "page": int(r["page"] or 0),
            "lineStart": r["line_start"],
            "lineEnd": r["line_end"],
            "sourceText": r["source_text"],
        }
