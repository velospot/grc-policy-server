"""Archive data/uploads/ with a timestamp snapshot.

Creates data/archives/{timestamp}/ with a full copy of uploads/ and a manifest.json
summarising all documents and comparison traces captured at that point in time.

Usage:
    uv run python scripts/archive_uploads.py
    uv run python scripts/archive_uploads.py --uploads-dir data/uploads --archive-root data/archives
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def _read_metadata(doc_dir: Path) -> dict | None:
    p = doc_dir / "metadata.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _trace_summary(trace_path: Path) -> dict | None:
    try:
        data = json.loads(trace_path.read_text())
    except Exception:
        return None

    cp = data.get("checkpoints", {})
    align = cp.get("alignmentResults", {})
    dr = cp.get("diffRecords", {})
    crs = dr.get("changeRecords", []) if isinstance(dr, dict) else []

    matched = align.get("matchedNodes") or 0
    ul = align.get("unmatchedLeft") or 0
    ur = align.get("unmatchedRight") or 0
    total = matched + ul + ur
    match_rate = round(matched / total, 4) if total else 0.0

    sev: dict[str, int] = {}
    if isinstance(crs, list):
        for r in crs:
            s = r.get("changeSeverity", "unknown")
            sev[s] = sev.get(s, 0) + 1

    conf_vals = []
    conf_raw = dr.get("confidenceDistribution", []) if isinstance(dr, dict) else []
    if isinstance(conf_raw, list):
        for c in conf_raw:
            if isinstance(c, (int, float)):
                conf_vals.append(c)
            elif isinstance(c, dict):
                conf_vals.append(c.get("confidence", 0.0))

    return {
        "doc1_id": data.get("doc1Id") or data.get("checkpoints", {}).get("doc1Id", ""),
        "doc2_id": data.get("doc2Id") or data.get("checkpoints", {}).get("doc2Id", ""),
        "trace_file": trace_path.name,
        "matched": matched,
        "unmatched_left": ul,
        "unmatched_right": ur,
        "match_rate": match_rate,
        "change_counts": dr.get("changeCounts", {}) if isinstance(dr, dict) else {},
        "severity": sev,
        "confidence_mean": round(sum(conf_vals) / len(conf_vals), 4) if conf_vals else None,
        "numeric_changes": dr.get("numericChanges") if isinstance(dr, dict) else None,
        "table_changes": dr.get("tableChanges") if isinstance(dr, dict) else None,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def archive(uploads_dir: Path, archive_root: Path) -> Path:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_dir = archive_root / ts
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Copy uploads tree
    dest = archive_dir / "uploads"
    print(f"Copying {uploads_dir} → {dest} …", flush=True)
    shutil.copytree(uploads_dir, dest)
    print(f"  Copied {sum(1 for _ in dest.rglob('*') if _.is_file())} files")

    # Collect document metadata
    documents = []
    for doc_dir in sorted(uploads_dir.iterdir()):
        if doc_dir.name.startswith("_") or not doc_dir.is_dir():
            continue
        meta = _read_metadata(doc_dir)
        if meta:
            documents.append({
                "id": meta.get("id"),
                "name": meta.get("name"),
                "family": meta.get("document_family"),
                "stable_id": meta.get("document_stable_id"),
                "chunks_stored": meta.get("chunks_stored"),
                "upload_date": meta.get("upload_date"),
                "size_bytes": meta.get("size_bytes"),
                "ocr_used": (meta.get("ocr") or {}).get("used", False),
            })

    # Collect comparison trace summaries
    traces = []
    trace_dir = uploads_dir / "_comparison_traces"
    if trace_dir.is_dir():
        for tf in sorted(trace_dir.glob("*.json")):
            summary = _trace_summary(tf)
            if summary:
                traces.append(summary)

    manifest = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "documents": documents,
        "comparison_traces": traces,
        "summary": {
            "document_count": len(documents),
            "trace_count": len(traces),
            "avg_match_rate": (
                round(sum(t["match_rate"] for t in traces) / len(traces), 4)
                if traces else None
            ),
        },
    }
    manifest_path = archive_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return archive_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive data/uploads/ with timestamp")
    parser.add_argument(
        "--uploads-dir",
        type=Path,
        default=Path("data/uploads"),
        help="Path to uploads directory",
    )
    parser.add_argument(
        "--archive-root",
        type=Path,
        default=Path("data/archives"),
        help="Root directory for archives",
    )
    args = parser.parse_args()

    if not args.uploads_dir.is_dir():
        print(f"ERROR: uploads dir not found: {args.uploads_dir}", file=sys.stderr)
        sys.exit(1)

    args.archive_root.mkdir(parents=True, exist_ok=True)
    archive_dir = archive(args.uploads_dir, args.archive_root)

    manifest = json.loads((archive_dir / "manifest.json").read_text())
    s = manifest["summary"]

    print()
    print(f"Archive created: {archive_dir}")
    print(f"  Documents : {s['document_count']}")
    print(f"  Traces    : {s['trace_count']}")
    if s["avg_match_rate"] is not None:
        print(f"  Avg match : {s['avg_match_rate']:.1%}")
    print(f"  Manifest  : {archive_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
