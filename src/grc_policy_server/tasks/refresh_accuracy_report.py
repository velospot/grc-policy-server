from __future__ import annotations

import dataclasses
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from grc_policy_server.core.celery_app import celery_app
from grc_policy_server.core.config import settings

logger = logging.getLogger(__name__)

_MAX_SNAPSHOTS = 30


@celery_app.task(name="grc_policy_server.tasks.refresh_accuracy_report")
def refresh_accuracy_report() -> dict[str, int]:
    from grc_policy_server.services.ingestion.accuracy_evaluator import AccuracyEvaluator

    upload_root = Path(settings.upload_root)
    if not upload_root.exists():
        logger.warning("upload_root does not exist — nothing to evaluate")
        return {"documents_evaluated": 0, "errors": 0}

    evaluator = AccuracyEvaluator(upload_root=upload_root)
    results: list[dict] = []
    errors = 0

    for doc_dir in sorted(upload_root.iterdir()):
        if not doc_dir.is_dir() or doc_dir.name.startswith("_"):
            continue
        try:
            metrics = evaluator.evaluate_document(doc_dir.name)
            results.append(dataclasses.asdict(metrics))
        except Exception:
            logger.exception(
                "accuracy evaluation failed document_id=%s", doc_dir.name
            )
            errors += 1

    report_path = upload_root / "_accuracy_report.json"
    report_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Persist a timestamped snapshot for drift tracking (rolling window of _MAX_SNAPSHOTS)
    snapshots_dir = upload_root / "_accuracy_snapshots"
    snapshots_dir.mkdir(exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = snapshots_dir / f"{ts}.json"
    snapshot_path.write_text(
        json.dumps({"timestamp": ts, "results": results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    # Prune oldest snapshots beyond _MAX_SNAPSHOTS
    existing = sorted(snapshots_dir.glob("*.json"))
    for old in existing[:-_MAX_SNAPSHOTS]:
        try:
            old.unlink()
        except OSError:
            pass

    logger.info(
        "accuracy report refreshed documents=%s errors=%s snapshot=%s", len(results), errors, ts
    )
    return {"documents_evaluated": len(results), "errors": errors}
