"""Human review queue backed by PostgreSQL with a local file fallback.

Follows the CanonicalDocumentStore pattern (psycopg + _postgres_disabled flag).

Items arrive when the OntologyClassifier confidence is below the configured
threshold (default 0.70).  Auditors resolve items via the review-queue API.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from grc_policy_server.core.config import settings

try:
    import psycopg
    from psycopg.types.json import Jsonb
except Exception:
    psycopg = None  # type: ignore[assignment]
    Jsonb = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_FALLBACK_DIR = "_human_review"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS human_review_queue (
    id          SERIAL PRIMARY KEY,
    node_id     TEXT NOT NULL,
    document_id TEXT NOT NULL,
    classification_attempt JSONB,
    confidence  FLOAT NOT NULL DEFAULT 0.0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT
);
CREATE INDEX IF NOT EXISTS human_review_queue_doc
    ON human_review_queue (document_id, resolved_at);
"""


class HumanReviewQueue:
    """Persist and retrieve low-confidence ontology classification items.

    PostgreSQL is the canonical backing store.  When unavailable, items are
    appended to a JSON file under ``upload_root/_human_review/{document_id}.json``
    so the upload pipeline never fails due to database issues.
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

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def enqueue(
        self,
        *,
        node_id: str,
        document_id: str,
        classification: dict,
        confidence: float,
    ) -> None:
        """Enqueue a single item for human review."""
        if not self._postgres_disabled and self.database_url and psycopg is not None:
            try:
                self._pg_enqueue(
                    node_id=node_id,
                    document_id=document_id,
                    classification=classification,
                    confidence=confidence,
                )
                return
            except Exception:
                logger.exception(
                    "human_review_queue PG enqueue failed, using file fallback"
                )
                self._postgres_disabled = True
        self._file_enqueue(
            node_id=node_id,
            document_id=document_id,
            classification=classification,
            confidence=confidence,
        )

    def enqueue_batch(
        self,
        *,
        document_id: str,
        chunks: list,
        classifications: list,
    ) -> None:
        """Enqueue all low-confidence classification results for a document."""
        for chunk, cls in zip(chunks, classifications):
            if getattr(cls, "review_flag", False):
                node_id = str(
                    getattr(chunk, "chunk_id", None)
                    or getattr(chunk, "node_id", None)
                    or ""
                )
                self.enqueue(
                    node_id=node_id,
                    document_id=document_id,
                    classification={
                        "ontology_type": cls.ontology_type,
                        "confidence": cls.confidence,
                        "properties": cls.properties,
                    },
                    confidence=cls.confidence,
                )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_pending(self, *, document_id: str) -> list[dict]:
        """Return all unresolved review items for a document."""
        if not self._postgres_disabled and self.database_url and psycopg is not None:
            try:
                return self._pg_list_pending(document_id=document_id)
            except Exception:
                logger.exception(
                    "human_review_queue PG list failed, using file fallback"
                )
                self._postgres_disabled = True
        return self._file_list_pending(document_id=document_id)

    def resolve(self, *, item_id: int, resolved_by: str = "") -> None:
        """Mark a review item as resolved."""
        if not self._postgres_disabled and self.database_url and psycopg is not None:
            try:
                with psycopg.connect(self.database_url, autocommit=True) as conn:
                    self._ensure_schema(conn)
                    conn.execute(
                        "UPDATE human_review_queue "
                        "SET resolved_at = %s, resolved_by = %s "
                        "WHERE id = %s AND resolved_at IS NULL",
                        (datetime.now(UTC), resolved_by, item_id),
                    )
                return
            except Exception:
                logger.exception("human_review_queue PG resolve failed")
                self._postgres_disabled = True

    # ------------------------------------------------------------------
    # PostgreSQL implementation
    # ------------------------------------------------------------------

    def _pg_enqueue(
        self,
        *,
        node_id: str,
        document_id: str,
        classification: dict,
        confidence: float,
    ) -> None:
        with psycopg.connect(self.database_url, autocommit=True) as conn:
            self._ensure_schema(conn)
            conn.execute(
                "INSERT INTO human_review_queue "
                "(node_id, document_id, classification_attempt, confidence) "
                "VALUES (%s, %s, %s, %s)",
                (
                    node_id,
                    document_id,
                    Jsonb(classification),
                    confidence,
                ),
            )

    def _pg_list_pending(self, *, document_id: str) -> list[dict]:
        with psycopg.connect(self.database_url, autocommit=False) as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT id, node_id, document_id, classification_attempt, "
                "confidence, created_at "
                "FROM human_review_queue "
                "WHERE document_id = %s AND resolved_at IS NULL "
                "ORDER BY created_at DESC",
                (document_id,),
            ).fetchall()
        return [
            {
                "id": row[0],
                "node_id": row[1],
                "document_id": row[2],
                "classification_attempt": row[3],
                "confidence": row[4],
                "created_at": row[5].isoformat() if row[5] else None,
            }
            for row in rows
        ]

    def _ensure_schema(self, conn) -> None:
        for stmt in _CREATE_TABLE_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass  # index already exists, etc.

    # ------------------------------------------------------------------
    # File fallback
    # ------------------------------------------------------------------

    def _fallback_path(self, document_id: str) -> Path:
        d = self.upload_root / _FALLBACK_DIR
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{document_id}.json"

    def _file_enqueue(
        self,
        *,
        node_id: str,
        document_id: str,
        classification: dict,
        confidence: float,
    ) -> None:
        path = self._fallback_path(document_id)
        items: list[dict] = []
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        items.append(
            {
                "id": len(items) + 1,
                "node_id": node_id,
                "document_id": document_id,
                "classification_attempt": classification,
                "confidence": confidence,
                "created_at": datetime.now(UTC).isoformat(),
                "resolved_at": None,
                "resolved_by": None,
            }
        )
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(path)

    def _file_list_pending(self, *, document_id: str) -> list[dict]:
        path = self._fallback_path(document_id)
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
            return [i for i in items if not i.get("resolved_at")]
        except Exception:
            return []
