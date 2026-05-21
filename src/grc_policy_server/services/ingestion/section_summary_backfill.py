from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grc_policy_server.services.graph.graph_neo4j_client import Neo4jClient
from grc_policy_server.services.ingestion.hierarchy_builder import (
    summarize_section_fragments,
)
from grc_policy_server.services.ingestion.hierarchy_models import HierarchyNode
from grc_policy_server.services.vector.weaviate_client import WeaviateClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SectionSummaryBackfillResult:
    documents_seen: int = 0
    documents_updated: int = 0
    documents_skipped: int = 0
    section_nodes_updated: int = 0
    vector_records_upserted: int = 0


class SectionSummaryBackfillService:
    def __init__(
        self,
        *,
        upload_root: Path,
        weaviate: WeaviateClient | None = None,
        neo4j: Neo4jClient | None = None,
    ) -> None:
        self.upload_root = upload_root
        self.weaviate = weaviate
        self.neo4j = neo4j

    def backfill_all(self) -> SectionSummaryBackfillResult:
        seen = 0
        updated = 0
        skipped = 0
        section_nodes_updated = 0
        vector_records_upserted = 0

        if not self.upload_root.exists():
            return SectionSummaryBackfillResult()

        for document_dir in sorted(self.upload_root.iterdir()):
            if not document_dir.is_dir() or document_dir.name.startswith("_"):
                continue
            seen += 1
            result = self._backfill_document_dir(document_dir)
            if result is None:
                skipped += 1
                continue
            updated += 1
            section_nodes_updated += result["section_nodes_updated"]
            vector_records_upserted += result["vector_records_upserted"]

        return SectionSummaryBackfillResult(
            documents_seen=seen,
            documents_updated=updated,
            documents_skipped=skipped,
            section_nodes_updated=section_nodes_updated,
            vector_records_upserted=vector_records_upserted,
        )

    def _backfill_document_dir(self, document_dir: Path) -> dict[str, int] | None:
        metadata_path = document_dir / "metadata.json"
        hierarchy_path = document_dir / "hierarchy.json"
        metadata = self._read_json(metadata_path)
        hierarchy = self._read_json(hierarchy_path)
        if not metadata or not hierarchy:
            return None

        nodes = hierarchy.get("nodes")
        if not isinstance(nodes, list):
            return None

        refreshed_nodes, updated_sections = self._refresh_section_summaries(nodes)
        if updated_sections == 0:
            return None

        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        hierarchy_metadata = dict(hierarchy.get("metadata") or {})
        hierarchy_metadata["section_summary_backfill_at"] = timestamp
        hierarchy["metadata"] = hierarchy_metadata
        hierarchy["nodes"] = refreshed_nodes
        hierarchy_path.write_text(json.dumps(hierarchy, indent=2), encoding="utf-8")

        metadata["section_summary_backfill_at"] = timestamp
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        vector_records = self._section_vector_records(refreshed_nodes)
        if vector_records and self.weaviate is not None:
            self.weaviate.upsert_chunks(vector_records)

        if self.neo4j is not None:
            self.neo4j.upsert_document_hierarchy(
                document_id=str(metadata.get("id") or document_dir.name),
                filename=str(metadata.get("name") or document_dir.name),
                document_stable_id=str(hierarchy.get("documentStableId") or ""),
                document_family=str(hierarchy.get("documentFamily") or ""),
                content_hash=str(hierarchy.get("contentHash") or ""),
                nodes=refreshed_nodes,
                metadata=hierarchy_metadata,
            )

        logger.info(
            "backfilled section summaries document_id=%s sections=%s vectors=%s",
            metadata.get("id") or document_dir.name,
            updated_sections,
            len(vector_records),
        )
        return {
            "section_nodes_updated": updated_sections,
            "vector_records_upserted": len(vector_records),
        }

    def _refresh_section_summaries(
        self,
        nodes: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        refreshed_nodes: list[dict[str, Any]] = []
        section_buffers: dict[tuple[str, ...], list[str]] = defaultdict(list)

        for raw_node in nodes:
            node = dict(raw_node)
            metadata = dict(node.get("metadata") or {})
            node["metadata"] = metadata
            refreshed_nodes.append(node)

            if str(node.get("node_type") or "") not in {"clause", "table"}:
                continue
            if bool(node.get("excluded_from_index", False)):
                continue
            section_titles = tuple(str(item) for item in node.get("section_titles") or [])
            if not section_titles:
                continue
            clean_text = str(metadata.get("clean_text") or node.get("text") or "").strip()
            if not clean_text:
                continue
            for depth in range(1, len(section_titles) + 1):
                section_buffers[section_titles[:depth]].append(clean_text)

        updated_sections = 0
        for node in refreshed_nodes:
            if str(node.get("node_type") or "") != "section":
                continue
            section_titles = tuple(str(item) for item in node.get("section_titles") or [])
            summary = summarize_section_fragments(section_buffers.get(section_titles, []))
            metadata = dict(node.get("metadata") or {})
            if (
                str(metadata.get("summary_text") or "") != str(summary["summary_text"])
                or list(metadata.get("summary_obligations") or [])
                != list(summary["obligations"])
                or list(metadata.get("summary_numbers") or [])
                != list(summary["numbers"])
            ):
                updated_sections += 1
            metadata["summary_text"] = summary["summary_text"]
            metadata["summary_obligations"] = summary["obligations"]
            metadata["summary_numbers"] = summary["numbers"]
            metadata["summary_sentences"] = summary["sentence_count"]
            node["metadata"] = metadata

        return refreshed_nodes, updated_sections

    def _section_vector_records(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for node in nodes:
            if str(node.get("node_type") or "") != "section":
                continue
            if not bool(node.get("indexable", False)) or bool(
                node.get("excluded_from_index", False)
            ):
                continue
            records.append(HierarchyNode.from_graph_record(node).to_vector_record())
        return records

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
