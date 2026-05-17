from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from grc_policy_server.models.schemas import ComparisonResult

logger = logging.getLogger(__name__)


class ComparisonCacheStore:
    """Persist and retrieve comparison output by ordered document pair."""

    cached_job_prefix = "cached-"
    # Default TTL: 24 hours. Set to 0 to disable expiry.
    default_ttl_sec: float = 86400.0

    def __init__(self, *, upload_root: Path, cache_ttl_sec: float | None = None):
        self.upload_root = upload_root
        self.cache_dir = self.upload_root / "_comparison_cache"
        self.cache_ttl_sec = cache_ttl_sec if cache_ttl_sec is not None else self.default_ttl_sec

    # Bump when the comparison algorithm changes to auto-invalidate stale cached results.
    CACHE_VERSION = "v2"

    def cache_key_for_pair(self, *, doc1_id: str, doc2_id: str) -> str:
        normalized = f"{self.CACHE_VERSION}::{doc1_id.strip()}::{doc2_id.strip()}"
        return sha256(normalized.encode("utf-8")).hexdigest()

    def cached_job_id_for_pair(self, *, doc1_id: str, doc2_id: str) -> str:
        return f"{self.cached_job_prefix}{self.cache_key_for_pair(doc1_id=doc1_id, doc2_id=doc2_id)}"

    def is_cached_job_id(self, job_id: str) -> bool:
        return str(job_id or "").startswith(self.cached_job_prefix)

    def save_for_pair(
        self,
        *,
        doc1_id: str,
        doc2_id: str,
        result: ComparisonResult,
    ) -> str:
        key = self.cache_key_for_pair(doc1_id=doc1_id, doc2_id=doc2_id)
        self.save_for_key(key=key, doc1_id=doc1_id, doc2_id=doc2_id, result=result)
        return key

    def save_for_key(
        self,
        *,
        key: str,
        doc1_id: str,
        doc2_id: str,
        result: ComparisonResult,
    ) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "cacheKey": key,
            "doc1Id": doc1_id,
            "doc2Id": doc2_id,
            "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "result": result.model_dump(mode="json"),
        }
        target = self._path_for_key(key)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)

    def load_for_pair(self, *, doc1_id: str, doc2_id: str) -> ComparisonResult | None:
        key = self.cache_key_for_pair(doc1_id=doc1_id, doc2_id=doc2_id)
        return self.load_for_key(key)

    def load_for_cached_job(self, *, job_id: str) -> ComparisonResult | None:
        key = self._extract_cached_key(job_id)
        if not key:
            return None
        return self.load_for_key(key)

    def load_for_key(self, key: str) -> ComparisonResult | None:
        target = self._path_for_key(key)
        if not target.exists():
            return None
        payload = self._read_json(target)
        if not payload:
            return None

        if self.cache_ttl_sec > 0:
            updated_at_str = payload.get("updatedAt")
            if updated_at_str:
                try:
                    updated_at = datetime.fromisoformat(
                        updated_at_str.replace("Z", "+00:00")
                    )
                    age_sec = (datetime.now(UTC) - updated_at).total_seconds()
                    if age_sec > self.cache_ttl_sec:
                        logger.debug("comparison cache expired for key=%s age=%.0fs", key, age_sec)
                        return None
                except Exception:
                    logger.warning("could not parse updatedAt for cache key=%s", key)

        raw = payload.get("result")
        if not isinstance(raw, dict):
            return None
        try:
            return ComparisonResult.model_validate(raw)
        except Exception:
            logger.warning("failed to deserialize cached comparison for key=%s", key)
            return None

    def _path_for_key(self, key: str) -> Path:
        return self.cache_dir / f"{str(key or '').strip()}.json"

    def _extract_cached_key(self, job_id: str) -> str | None:
        value = str(job_id or "").strip()
        if not value.startswith(self.cached_job_prefix):
            return None
        key = value.removeprefix(self.cached_job_prefix).strip()
        return key or None

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
