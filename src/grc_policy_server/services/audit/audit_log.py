"""Immutable comparison audit log — append-only PostgreSQL table.

Follows the CanonicalDocumentStore psycopg pattern:
  - PostgreSQL primary store with `_postgres_disabled` guard.
  - JSONL file fallback when PostgreSQL is unavailable.
  - `CREATE OR REPLACE RULE` blocks UPDATE and DELETE at the database level.

Event types emitted by the comparison pipeline:
  "comparison_started"    — beginning of each compare() call
  "comparison_completed"  — end of compare() call with summary stats
  "evidence_extracted"    — one per MODIFIED node pair processed by EvidenceExtractionAgent
"""
from __future__ import annotations

import hashlib
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

_FALLBACK_FILE = "_audit_log.jsonl"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id             SERIAL PRIMARY KEY,
    event_type     TEXT NOT NULL,
    entity_id      TEXT,
    entity_type    TEXT,
    doc_id         TEXT,
    comparison_id  TEXT,
    actor          TEXT,
    model_version  TEXT,
    input_hash     TEXT,
    output_hash    TEXT,
    confidence     FLOAT,
    payload        JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS audit_log_comparison
    ON audit_log (comparison_id)
    WHERE comparison_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS audit_log_doc
    ON audit_log (doc_id)
    WHERE doc_id IS NOT NULL;
"""

# These rules make the table append-only at the PostgreSQL level.
# DO INSTEAD NOTHING silently ignores UPDATE/DELETE — no error raised, no rows changed.
_IMMUTABILITY_RULES = """
CREATE OR REPLACE RULE audit_log_no_update
    AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
CREATE OR REPLACE RULE audit_log_no_delete
    AS ON DELETE TO audit_log DO INSTEAD NOTHING;
"""


class AuditLogStore:
    """Append-only audit log for every comparison event.

    PostgreSQL is the canonical store.  When unavailable, events are appended
    as JSON lines to ``upload_root/_audit_log.jsonl`` so the comparison pipeline
    never fails because of database issues.
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

    def log_event(
        self,
        *,
        event_type: str,
        entity_id: str | None = None,
        entity_type: str | None = None,
        doc_id: str | None = None,
        comparison_id: str | None = None,
        actor: str = "system",
        model_version: str = "",
        input_hash: str | None = None,
        output_hash: str | None = None,
        confidence: float | None = None,
        payload: dict | None = None,
    ) -> None:
        """Append one audit event.  Never raises — logs warnings on failure."""
        record = {
            "event_type": event_type,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "doc_id": doc_id,
            "comparison_id": comparison_id,
            "actor": actor,
            "model_version": model_version,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "confidence": confidence,
            "payload": payload or {},
            "created_at": datetime.now(UTC).isoformat(),
        }

        if not self._postgres_disabled and self.database_url and psycopg is not None:
            try:
                self._pg_insert(record)
                return
            except Exception:
                logger.exception(
                    "audit_log PG insert failed — using JSONL file fallback"
                )
                self._postgres_disabled = True

        self._file_append(record)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_for_comparison(self, *, comparison_id: str) -> list[dict]:
        """Return all events for a specific comparison_id, newest first."""
        if not self._postgres_disabled and self.database_url and psycopg is not None:
            try:
                return self._pg_query(
                    "SELECT * FROM audit_log WHERE comparison_id = %s ORDER BY created_at DESC",
                    (comparison_id,),
                )
            except Exception:
                logger.exception("audit_log PG query failed")
                self._postgres_disabled = True
        return self._file_query(lambda r: r.get("comparison_id") == comparison_id)

    def list_for_document(self, *, doc_id: str) -> list[dict]:
        """Return all events for a specific doc_id, newest first."""
        if not self._postgres_disabled and self.database_url and psycopg is not None:
            try:
                return self._pg_query(
                    "SELECT * FROM audit_log WHERE doc_id = %s ORDER BY created_at DESC",
                    (doc_id,),
                )
            except Exception:
                logger.exception("audit_log PG query failed")
                self._postgres_disabled = True
        return self._file_query(lambda r: r.get("doc_id") == doc_id)

    # ------------------------------------------------------------------
    # PostgreSQL
    # ------------------------------------------------------------------

    def _pg_insert(self, record: dict) -> None:
        with psycopg.connect(self.database_url, autocommit=True) as conn:
            self._ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO audit_log (
                    event_type, entity_id, entity_type, doc_id, comparison_id,
                    actor, model_version, input_hash, output_hash, confidence, payload
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record["event_type"],
                    record.get("entity_id"),
                    record.get("entity_type"),
                    record.get("doc_id"),
                    record.get("comparison_id"),
                    record.get("actor") or "system",
                    record.get("model_version") or "",
                    record.get("input_hash"),
                    record.get("output_hash"),
                    record.get("confidence"),
                    Jsonb(record.get("payload") or {}),
                ),
            )

    def _pg_query(self, sql: str, params: tuple) -> list[dict]:
        with psycopg.connect(self.database_url, autocommit=False) as conn:
            self._ensure_schema(conn)
            rows = conn.execute(sql, params).fetchall()
            cols = [
                "id", "event_type", "entity_id", "entity_type", "doc_id",
                "comparison_id", "actor", "model_version", "input_hash",
                "output_hash", "confidence", "payload", "created_at",
            ]
            return [
                {col: (val.isoformat() if hasattr(val, "isoformat") else val)
                 for col, val in zip(cols, row)}
                for row in rows
            ]

    def _ensure_schema(self, conn) -> None:
        for stmt in _CREATE_TABLE_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass
        for stmt in _IMMUTABILITY_RULES.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # File fallback (JSONL append)
    # ------------------------------------------------------------------

    def _fallback_path(self) -> Path:
        return self.upload_root / _FALLBACK_FILE

    def _file_append(self, record: dict) -> None:
        try:
            path = self._fallback_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            logger.exception("audit_log file fallback failed — event lost")

    def _file_query(self, predicate) -> list[dict]:
        path = self._fallback_path()
        if not path.exists():
            return []
        results: list[dict] = []
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if predicate(record):
                            results.append(record)
                    except Exception:
                        pass
        except Exception:
            logger.exception("audit_log file read failed")
        return list(reversed(results))  # newest first
