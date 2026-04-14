from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grc_policy_server.core.config import settings
from grc_policy_server.services.documents.canonical_models import (
    CanonicalNode,
    canonical_nodes_from_hierarchy,
)

try:  # pragma: no cover - exercised in integration environments with PostgreSQL.
    import psycopg
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover - keeps local tests usable without the extra.
    psycopg = None  # type: ignore[assignment]
    Jsonb = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class CanonicalDocumentStore:
    """Store raw Docling JSON and the normalized node tree.

    PostgreSQL is the canonical backing store when available. A JSON file copy is
    always written under the document directory to keep local development and
    unit tests deterministic, and to provide a fallback when PostgreSQL is not
    reachable during a one-off run.
    """

    def __init__(
        self,
        *,
        database_url: str | None = None,
        upload_root: Path | None = None,
    ) -> None:
        self.database_url = settings.database_url if database_url is None else database_url
        self.upload_root = upload_root or Path(settings.upload_root)
        self._postgres_disabled = False

    def save_document(
        self,
        *,
        document_id: str,
        filename: str,
        content_hash: str,
        docling_json: dict[str, Any],
        hierarchy: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        version_id: str = "1.0",
    ) -> list[CanonicalNode]:
        canonical_nodes = canonical_nodes_from_hierarchy(
            hierarchy,
            version_id=version_id,
        )
        payload = {
            "documentId": document_id,
            "filename": filename,
            "contentHash": content_hash,
            "versionId": version_id,
            "metadata": metadata or {},
            "retrievalArtifacts": _retrieval_artifacts_from_hierarchy(hierarchy),
            "nodes": [node.to_dict() for node in canonical_nodes],
        }

        self._write_file_copy(
            document_id=document_id,
            docling_json=docling_json,
            canonical_payload=payload,
        )
        self._try_save_postgres(
            document_id=document_id,
            filename=filename,
            content_hash=content_hash,
            version_id=version_id,
            docling_json=docling_json,
            canonical_payload=payload,
            nodes=canonical_nodes,
            metadata=metadata or {},
        )
        return canonical_nodes

    def load_nodes(self, document_id: str) -> list[CanonicalNode]:
        doc_id = document_id.strip()
        if not doc_id:
            return []

        nodes = self._try_load_postgres(doc_id)
        if nodes:
            return nodes

        nodes = self._load_file_nodes(doc_id)
        if nodes:
            return nodes

        hierarchy_path = self.upload_root / doc_id / "hierarchy.json"
        try:
            hierarchy = json.loads(hierarchy_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return canonical_nodes_from_hierarchy(hierarchy)

    def load_comparison_nodes(self, document_id: str) -> list[dict[str, Any]]:
        return [node.to_comparison_record() for node in self.load_nodes(document_id)]

    def load_debug_artifacts(self, document_id: str) -> dict[str, Any]:
        doc_id = document_id.strip()
        if not doc_id:
            return {}

        postgres_payload = self._try_load_postgres_artifacts(doc_id)
        if postgres_payload:
            return postgres_payload

        target_dir = self.upload_root / doc_id
        return {
            "documentId": doc_id,
            "rawDoclingJson": _read_json(target_dir / "raw_docling.json"),
            "normalizedTreeJson": _read_json(target_dir / "canonical_nodes.json"),
            "rawDoclingPath": str(target_dir / "raw_docling.json"),
            "normalizedTreePath": str(target_dir / "canonical_nodes.json"),
            "hierarchyPath": str(target_dir / "hierarchy.json"),
            "hierarchyJson": _read_json(target_dir / "hierarchy.json"),
        }

    def delete_document(self, document_id: str) -> None:
        doc_id = document_id.strip()
        if not doc_id or self._postgres_disabled or not self.database_url or psycopg is None:
            return
        try:
            with psycopg.connect(self.database_url, autocommit=True) as conn:
                self._ensure_schema(conn)
                conn.execute(
                    "DELETE FROM canonical_documents WHERE document_id = %s",
                    (doc_id,),
                )
        except Exception:
            logger.exception("failed to delete canonical document_id=%s", doc_id)
            self._postgres_disabled = True

    def _try_save_postgres(
        self,
        *,
        document_id: str,
        filename: str,
        content_hash: str,
        version_id: str,
        docling_json: dict[str, Any],
        canonical_payload: dict[str, Any],
        nodes: list[CanonicalNode],
        metadata: dict[str, Any],
    ) -> None:
        if self._postgres_disabled or not self.database_url or psycopg is None:
            return
        try:
            with psycopg.connect(self.database_url, autocommit=True) as conn:
                self._ensure_schema(conn)
                now = datetime.now(UTC)
                conn.execute(
                    """
                    INSERT INTO canonical_documents (
                        document_id,
                        filename,
                        content_hash,
                        version_id,
                        raw_docling_json,
                        normalized_tree_json,
                        metadata_json,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (document_id) DO UPDATE SET
                        filename = EXCLUDED.filename,
                        content_hash = EXCLUDED.content_hash,
                        version_id = EXCLUDED.version_id,
                        raw_docling_json = EXCLUDED.raw_docling_json,
                        normalized_tree_json = EXCLUDED.normalized_tree_json,
                        metadata_json = EXCLUDED.metadata_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        document_id,
                        filename,
                        content_hash,
                        version_id,
                        Jsonb(docling_json),
                        Jsonb(canonical_payload),
                        Jsonb(metadata),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "DELETE FROM canonical_document_nodes WHERE document_id = %s",
                    (document_id,),
                )
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO canonical_document_nodes (
                            node_id,
                            document_id,
                            version_id,
                            parent_id,
                            node_type,
                            section_label,
                            heading_path,
                            order_index,
                            raw_text,
                            normalized_text,
                            page_from,
                            page_to,
                            bbox_refs,
                            language,
                            source_kind,
                            stable_id,
                            content_hash,
                            title,
                            metadata_json
                        )
                        VALUES (
                            %(node_id)s,
                            %(document_id)s,
                            %(version_id)s,
                            %(parent_id)s,
                            %(node_type)s,
                            %(section_label)s,
                            %(heading_path)s,
                            %(order_index)s,
                            %(raw_text)s,
                            %(normalized_text)s,
                            %(page_from)s,
                            %(page_to)s,
                            %(bbox_refs)s,
                            %(language)s,
                            %(source_kind)s,
                            %(stable_id)s,
                            %(content_hash)s,
                            %(title)s,
                            %(metadata_json)s
                        )
                        ON CONFLICT (node_id) DO UPDATE SET
                            parent_id = EXCLUDED.parent_id,
                            node_type = EXCLUDED.node_type,
                            section_label = EXCLUDED.section_label,
                            heading_path = EXCLUDED.heading_path,
                            order_index = EXCLUDED.order_index,
                            raw_text = EXCLUDED.raw_text,
                            normalized_text = EXCLUDED.normalized_text,
                            page_from = EXCLUDED.page_from,
                            page_to = EXCLUDED.page_to,
                            bbox_refs = EXCLUDED.bbox_refs,
                            language = EXCLUDED.language,
                            source_kind = EXCLUDED.source_kind,
                            stable_id = EXCLUDED.stable_id,
                            content_hash = EXCLUDED.content_hash,
                            title = EXCLUDED.title,
                            metadata_json = EXCLUDED.metadata_json
                        """,
                        [self._node_sql_payload(node) for node in nodes],
                    )
        except Exception:
            logger.exception(
                "failed to persist canonical document to PostgreSQL document_id=%s",
                document_id,
            )
            self._postgres_disabled = True

    def _try_load_postgres(self, document_id: str) -> list[CanonicalNode]:
        if self._postgres_disabled or not self.database_url or psycopg is None:
            return []
        try:
            with psycopg.connect(self.database_url, autocommit=True) as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    """
                    SELECT
                        node_id,
                        document_id,
                        version_id,
                        parent_id,
                        node_type,
                        section_label,
                        heading_path,
                        order_index,
                        raw_text,
                        normalized_text,
                        page_from,
                        page_to,
                        bbox_refs,
                        language,
                        source_kind,
                        stable_id,
                        content_hash,
                        title,
                        metadata_json
                    FROM canonical_document_nodes
                    WHERE document_id = %s
                    ORDER BY order_index ASC, node_id ASC
                    """,
                    (document_id,),
                ).fetchall()
        except Exception:
            logger.exception(
                "failed to load canonical nodes from PostgreSQL document_id=%s",
                document_id,
            )
            self._postgres_disabled = True
            return []

        return [
            CanonicalNode.from_dict(
                {
                    "node_id": row[0],
                    "document_id": row[1],
                    "version_id": row[2],
                    "parent_id": row[3],
                    "node_type": row[4],
                    "section_label": row[5],
                    "heading_path": row[6],
                    "order_index": row[7],
                    "raw_text": row[8],
                    "normalized_text": row[9],
                    "page_from": row[10],
                    "page_to": row[11],
                    "bbox_refs": row[12],
                    "language": row[13],
                    "source_kind": row[14],
                    "stable_id": row[15],
                    "content_hash": row[16],
                    "title": row[17],
                    "metadata": row[18],
                }
            )
            for row in rows
        ]

    def _try_load_postgres_artifacts(self, document_id: str) -> dict[str, Any]:
        if self._postgres_disabled or not self.database_url or psycopg is None:
            return {}
        try:
            with psycopg.connect(self.database_url, autocommit=True) as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    """
                    SELECT
                        document_id,
                        filename,
                        content_hash,
                        version_id,
                        raw_docling_json,
                        normalized_tree_json,
                        metadata_json
                    FROM canonical_documents
                    WHERE document_id = %s
                    """,
                    (document_id,),
                ).fetchone()
        except Exception:
            logger.exception(
                "failed to load canonical artifacts from PostgreSQL document_id=%s",
                document_id,
            )
            self._postgres_disabled = True
            return {}

        if not row:
            return {}
        return {
            "documentId": row[0],
            "filename": row[1],
            "contentHash": row[2],
            "versionId": row[3],
            "rawDoclingJson": row[4],
            "normalizedTreeJson": row[5],
            "metadata": row[6],
            "storage": "postgres",
        }

    def _write_file_copy(
        self,
        *,
        document_id: str,
        docling_json: dict[str, Any],
        canonical_payload: dict[str, Any],
    ) -> None:
        target_dir = self.upload_root / document_id
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "raw_docling.json").write_text(
            json.dumps(docling_json, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (target_dir / "canonical_nodes.json").write_text(
            json.dumps(canonical_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load_file_nodes(self, document_id: str) -> list[CanonicalNode]:
        target = self.upload_root / document_id / "canonical_nodes.json"
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return []
        raw_nodes = payload.get("nodes") if isinstance(payload, dict) else None
        if not isinstance(raw_nodes, list):
            return []
        return [
            CanonicalNode.from_dict(node)
            for node in raw_nodes
            if isinstance(node, dict)
        ]

    def _ensure_schema(self, conn: Any) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS canonical_documents (
                document_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                version_id TEXT NOT NULL DEFAULT '1.0',
                raw_docling_json JSONB NOT NULL,
                normalized_tree_json JSONB NOT NULL,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS canonical_document_nodes (
                node_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL
                    REFERENCES canonical_documents(document_id)
                    ON DELETE CASCADE,
                version_id TEXT NOT NULL DEFAULT '1.0',
                parent_id TEXT,
                node_type TEXT NOT NULL,
                section_label TEXT,
                heading_path JSONB NOT NULL DEFAULT '[]'::jsonb,
                order_index INTEGER NOT NULL,
                raw_text TEXT NOT NULL DEFAULT '',
                normalized_text TEXT NOT NULL DEFAULT '',
                page_from INTEGER,
                page_to INTEGER,
                bbox_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                language TEXT NOT NULL DEFAULT '',
                source_kind TEXT NOT NULL DEFAULT 'body',
                stable_id TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                title TEXT,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_canonical_nodes_document_order
            ON canonical_document_nodes(document_id, order_index)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_canonical_nodes_stable
            ON canonical_document_nodes(document_id, stable_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_canonical_nodes_type
            ON canonical_document_nodes(document_id, node_type)
            """
        )

    @staticmethod
    def _node_sql_payload(node: CanonicalNode) -> dict[str, Any]:
        return {
            "node_id": node.node_id,
            "document_id": node.document_id,
            "version_id": node.version_id,
            "parent_id": node.parent_id,
            "node_type": node.node_type,
            "section_label": node.section_label,
            "heading_path": Jsonb(node.heading_path),
            "order_index": node.order_index,
            "raw_text": node.raw_text,
            "normalized_text": node.normalized_text,
            "page_from": node.page_from,
            "page_to": node.page_to,
            "bbox_refs": Jsonb(node.bbox_refs),
            "language": node.language,
            "source_kind": node.source_kind,
            "stable_id": node.stable_id,
            "content_hash": node.content_hash,
            "title": node.title,
            "metadata_json": Jsonb(node.metadata),
        }


def _retrieval_artifacts_from_hierarchy(hierarchy: dict[str, Any]) -> dict[str, Any]:
    nodes = hierarchy.get("nodes") if isinstance(hierarchy, dict) else None
    if not isinstance(nodes, list):
        return {"retrievalChunkCount": 0, "chunkToNodeMapping": []}

    mapping: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if not bool(node.get("indexable", False)):
            continue
        node_id = str(node.get("node_id") or "")
        if not node_id:
            continue
        mapping.append(
            {
                "chunkId": node_id,
                "canonicalNodeId": node_id,
                "parentId": node.get("parent_id"),
                "nodeType": node.get("node_type"),
                "sectionPath": node.get("section_path"),
                "page": node.get("page_number"),
            }
        )
    return {
        "retrievalChunkCount": len(mapping),
        "chunkToNodeMapping": mapping,
    }


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
