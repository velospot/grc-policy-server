from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neo4j import GraphDatabase


@dataclass
class Neo4jExpandConfig:
    neighbor_window: int = 2  # ±N chunks around each hit in same section
    include_objects: bool = True


class Neo4jContextExpander:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self._driver.close()

    def expand_hits(
        self, hits: list[dict[str, Any]], cfg: Neo4jExpandConfig | None = None
    ) -> list[dict[str, Any]]:
        cfg = cfg or Neo4jExpandConfig()

        # Expect hits from Weaviate contain at least: doc_id, section_id, order_index
        seeds = [
            {
                "doc_id": h["doc_id"],
                "section_id": h["section_id"],
                "order_index": h["order_index"],
            }
            for h in hits
            if h.get("doc_id")
            and h.get("section_id")
            and isinstance(h.get("order_index"), int)
        ]
        if not seeds:
            return []

        with self._driver.session() as s:
            return s.execute_read(
                self._expand_tx, seeds, cfg.neighbor_window, cfg.include_objects
            )

    @staticmethod
    def _expand_tx(
        tx, seeds: list[dict[str, Any]], window: int, include_objects: bool
    ) -> list[dict[str, Any]]:
        """
        Expand each seed within same section by order_index range.
        Also optionally fetch tables/figures referenced by expanded chunks.
        """
        query = """
        UNWIND $seeds AS seed
        MATCH (sec:Section {section_id: seed.section_id})
        MATCH (sec)-[rel:CONTAINS]->(c:Chunk)
        WHERE c.doc_id = seed.doc_id
          AND c.order_index >= seed.order_index - $window
          AND c.order_index <= seed.order_index + $window
        WITH DISTINCT c
        OPTIONAL MATCH (c)-[:MENTIONS_TABLE]->(t:Table)
        OPTIONAL MATCH (c)-[:MENTIONS_FIGURE]->(f:Figure)
        RETURN
          c.chunk_id AS chunk_id,
          c.doc_id AS doc_id,
          c.section_id AS section_id,
          c.order_index AS order_index,
          c.page_start AS page_start,
          c.page_end AS page_end,
          c.docling_path AS docling_path,
          c.main_text AS main_text,
          collect(DISTINCT t.table_id) AS table_ids,
          collect(DISTINCT f.figure_id) AS figure_ids
        ORDER BY doc_id, section_id, order_index
        """
        # If include_objects is False, you can skip OPTIONAL MATCH by using a simpler query.
        if not include_objects:
            query = """
            UNWIND $seeds AS seed
            MATCH (sec:Section {section_id: seed.section_id})
            MATCH (sec)-[rel:CONTAINS]->(c:Chunk)
            WHERE c.doc_id = seed.doc_id
              AND c.order_index >= seed.order_index - $window
              AND c.order_index <= seed.order_index + $window
            RETURN
              c.chunk_id AS chunk_id,
              c.doc_id AS doc_id,
              c.section_id AS section_id,
              c.order_index AS order_index,
              c.page_start AS page_start,
              c.page_end AS page_end,
              c.docling_path AS docling_path,
              c.main_text AS main_text,
              [] AS table_ids,
              [] AS figure_ids
            ORDER BY doc_id, section_id, order_index
            """

        res = tx.run(query, seeds=seeds, window=window)
        rows = [dict(r) for r in res]

        # Deduplicate by chunk_id while preserving order
        seen = set()
        out = []
        for r in rows:
            cid = r["chunk_id"]
            if cid in seen:
                continue
            seen.add(cid)
            out.append(r)
        return out
