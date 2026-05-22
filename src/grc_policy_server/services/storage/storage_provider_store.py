from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grc_policy_server.core.config import settings

try:  # pragma: no cover - exercised in integration environments with PostgreSQL.
    import psycopg
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]
    Jsonb = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageProviderRecord:
    provider_id: str
    provider_type: str
    name: str
    config: dict[str, Any]
    secrets: dict[str, Any]
    created_at: str
    updated_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "providerId": self.provider_id,
            "providerType": self.provider_type,
            "name": self.name,
            "config": self.config,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class StorageProviderStore:
    """Persist storage provider configurations.

    PostgreSQL is preferred when available; a JSON file under UPLOAD_ROOT is
    always written as a deterministic fallback for local dev and tests.
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

    @property
    def _file_path(self) -> Path:
        return self.upload_root / "_storage_providers.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_providers(self) -> list[StorageProviderRecord]:
        records = self._try_list_postgres()
        if records:
            return records
        return self._read_file()

    def get_provider(self, provider_id: str) -> StorageProviderRecord | None:
        pid = provider_id.strip()
        if not pid:
            return None
        record = self._try_get_postgres(pid)
        if record is not None:
            return record
        for item in self._read_file():
            if item.provider_id == pid:
                return item
        return None

    def upsert_provider(
        self,
        *,
        provider_id: str,
        provider_type: str,
        name: str,
        config: dict[str, Any] | None = None,
        secrets: dict[str, Any] | None = None,
    ) -> StorageProviderRecord:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        existing = self.get_provider(provider_id)
        created_at = existing.created_at if existing is not None else now
        record = StorageProviderRecord(
            provider_id=provider_id,
            provider_type=provider_type,
            name=name,
            config=config or {},
            secrets=secrets or {},
            created_at=created_at,
            updated_at=now,
        )

        self._write_file(record)
        self._try_upsert_postgres(record)
        return record

    def delete_provider(self, provider_id: str) -> bool:
        pid = provider_id.strip()
        if not pid:
            return False
        deleted_postgres = self._try_delete_postgres(pid)
        deleted_file = self._delete_from_file(pid)
        return deleted_postgres or deleted_file

    # ------------------------------------------------------------------
    # File persistence (fallback + deterministic local dev)
    # ------------------------------------------------------------------

    def _read_file(self) -> list[StorageProviderRecord]:
        path = self._file_path
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("failed to read storage providers file=%s", path)
            return []
        if not isinstance(raw, list):
            return []
        out: list[StorageProviderRecord] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            out.append(
                StorageProviderRecord(
                    provider_id=str(item.get("providerId") or ""),
                    provider_type=str(item.get("providerType") or ""),
                    name=str(item.get("name") or ""),
                    config=dict(item.get("config") or {}),
                    secrets=dict(item.get("secrets") or {}),
                    created_at=str(item.get("createdAt") or ""),
                    updated_at=str(item.get("updatedAt") or ""),
                )
            )
        return [rec for rec in out if rec.provider_id]

    def _write_file(self, record: StorageProviderRecord) -> None:
        self.upload_root.mkdir(parents=True, exist_ok=True)
        items = self._read_file()
        updated: list[dict[str, Any]] = []
        seen = False
        for item in items:
            if item.provider_id == record.provider_id:
                updated.append(self._record_to_file_dict(record))
                seen = True
            else:
                updated.append(self._record_to_file_dict(item))
        if not seen:
            updated.append(self._record_to_file_dict(record))
        self._file_path.write_text(json.dumps(updated, indent=2), encoding="utf-8")

    def _delete_from_file(self, provider_id: str) -> bool:
        items = self._read_file()
        kept = [item for item in items if item.provider_id != provider_id]
        if len(kept) == len(items):
            return False
        self.upload_root.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(
            json.dumps([self._record_to_file_dict(item) for item in kept], indent=2),
            encoding="utf-8",
        )
        return True

    def _record_to_file_dict(self, record: StorageProviderRecord) -> dict[str, Any]:
        return {
            "providerId": record.provider_id,
            "providerType": record.provider_type,
            "name": record.name,
            "config": record.config,
            "secrets": record.secrets,
            "createdAt": record.created_at,
            "updatedAt": record.updated_at,
        }

    # ------------------------------------------------------------------
    # PostgreSQL persistence (preferred in production)
    # ------------------------------------------------------------------

    def _try_list_postgres(self) -> list[StorageProviderRecord]:
        if self._postgres_disabled or not self.database_url or psycopg is None:
            return []
        try:
            with psycopg.connect(self.database_url, autocommit=True) as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    """
                    SELECT provider_id, provider_type, name, config_json, secrets_json, created_at, updated_at
                    FROM storage_providers
                    ORDER BY name ASC
                    """
                ).fetchall()
            out: list[StorageProviderRecord] = []
            for row in rows:
                out.append(
                    StorageProviderRecord(
                        provider_id=str(row[0]),
                        provider_type=str(row[1]),
                        name=str(row[2]),
                        config=dict(row[3] or {}),
                        secrets=dict(row[4] or {}),
                        created_at=str(row[5]),
                        updated_at=str(row[6]),
                    )
                )
            return out
        except Exception:
            logger.exception("failed to list storage providers from postgres")
            self._postgres_disabled = True
            return []

    def _try_get_postgres(self, provider_id: str) -> StorageProviderRecord | None:
        if self._postgres_disabled or not self.database_url or psycopg is None:
            return None
        try:
            with psycopg.connect(self.database_url, autocommit=True) as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    """
                    SELECT provider_id, provider_type, name, config_json, secrets_json, created_at, updated_at
                    FROM storage_providers
                    WHERE provider_id = %s
                    """,
                    (provider_id,),
                ).fetchone()
            if not row:
                return None
            return StorageProviderRecord(
                provider_id=str(row[0]),
                provider_type=str(row[1]),
                name=str(row[2]),
                config=dict(row[3] or {}),
                secrets=dict(row[4] or {}),
                created_at=str(row[5]),
                updated_at=str(row[6]),
            )
        except Exception:
            logger.exception("failed to fetch storage provider from postgres")
            self._postgres_disabled = True
            return None

    def _try_upsert_postgres(self, record: StorageProviderRecord) -> None:
        if self._postgres_disabled or not self.database_url or psycopg is None:
            return
        try:
            with psycopg.connect(self.database_url, autocommit=True) as conn:
                self._ensure_schema(conn)
                conn.execute(
                    """
                    INSERT INTO storage_providers (
                        provider_id,
                        provider_type,
                        name,
                        config_json,
                        secrets_json,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (provider_id) DO UPDATE SET
                        provider_type = EXCLUDED.provider_type,
                        name = EXCLUDED.name,
                        config_json = EXCLUDED.config_json,
                        secrets_json = EXCLUDED.secrets_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        record.provider_id,
                        record.provider_type,
                        record.name,
                        Jsonb(record.config),
                        Jsonb(record.secrets),
                        record.created_at,
                        record.updated_at,
                    ),
                )
        except Exception:
            logger.exception("failed to upsert storage provider into postgres")
            self._postgres_disabled = True

    def _try_delete_postgres(self, provider_id: str) -> bool:
        if self._postgres_disabled or not self.database_url or psycopg is None:
            return False
        try:
            with psycopg.connect(self.database_url, autocommit=True) as conn:
                self._ensure_schema(conn)
                result = conn.execute(
                    "DELETE FROM storage_providers WHERE provider_id = %s",
                    (provider_id,),
                )
                return bool(getattr(result, "rowcount", 0))
        except Exception:
            logger.exception("failed to delete storage provider from postgres")
            self._postgres_disabled = True
            return False

    def _ensure_schema(self, conn) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS storage_providers (
                provider_id TEXT PRIMARY KEY,
                provider_type TEXT NOT NULL,
                name TEXT NOT NULL,
                config_json JSONB NOT NULL,
                secrets_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )

