from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ComparisonTraceStore:
    """Persist compare checkpoints for locating where information is lost."""

    def __init__(self, *, upload_root: Path) -> None:
        self.trace_dir = upload_root / "_comparison_traces"

    def save_trace(
        self,
        *,
        doc1_id: str,
        doc2_id: str,
        payload: dict[str, Any],
    ) -> Path | None:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        safe_doc1 = _safe_segment(doc1_id)
        safe_doc2 = _safe_segment(doc2_id)
        target = self.trace_dir / f"{safe_doc1}__{safe_doc2}__{timestamp}.json"
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            logger.exception(
                "failed to write comparison trace doc1_id=%s doc2_id=%s",
                doc1_id,
                doc2_id,
            )
            return None
        return target


def _safe_segment(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:80]
