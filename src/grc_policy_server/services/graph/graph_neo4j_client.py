from neo4j import GraphDatabase


class Neo4jClient:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            "bolt://neo4j:7687", auth=("neo4j", "password")
        )

    def create_chunk(self, doc_id, section, chunk_id):
        with self.driver.session() as session:
            session.run(
                """
            MERGE (d:Document {id: $doc_id})
            MERGE (s:Section {name: $section})
            MERGE (c:Chunk {id: $chunk_id})
            MERGE (d)-[:HAS_SECTION]->(s)
            MERGE (s)-[:HAS_CHUNK]->(c)
            """,
                doc_id=doc_id,
                section=section,
                chunk_id=chunk_id,
            )

    def resolve_section_path(self, chunk_id: str) -> str:
        with self.driver.session() as session:
            res = session.run(
                """
            MATCH (s:Section)-[:HAS_CHUNK]->(c:Chunk {id: $chunk_id})
            RETURN s.full_path AS path
            """,
                chunk_id=chunk_id,
            ).single()

            return res["path"] if res else "Unknown Section"
