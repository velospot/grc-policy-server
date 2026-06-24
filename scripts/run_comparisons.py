"""Run all comparison pairs offline using OfflineDiffEngine (no Celery/LLM needed).

Reads comparison_pairs_resolved.json (doc IDs populated by ingest_docs.py), runs
each pair with OfflineDiffEngine, saves traces to data/uploads/_comparison_traces/,
and prints a per-pair summary.

Usage:
    uv run python scripts/run_comparisons.py
    uv run python scripts/run_comparisons.py --pairs scripts/comparison_pairs_resolved.json --uploads-dir data/uploads
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load_doc_metadata(uploads_dir: Path, doc_id: str) -> dict:
    meta_path = uploads_dir / doc_id / "metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"No metadata for doc {doc_id}: {meta_path}")
    return json.loads(meta_path.read_text())


def run_comparison(
    doc1_id: str,
    doc2_id: str,
    testing_department: str,
    uploads_dir: Path,
    engine_type: str = "offline",
) -> dict:
    """Run one comparison and return a summary dict.

    engine_type: "offline" uses OfflineDiffEngine (no external services).
                 "real"    uses RealDiffEngine with Qdrant (QDRANT_URL env var required).
    """
    import os
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

    from grc_policy_server.models.schemas import Document
    from grc_policy_server.services.documents.canonical_store import CanonicalDocumentStore

    store = CanonicalDocumentStore(upload_root=uploads_dir)

    meta1 = _load_doc_metadata(uploads_dir, doc1_id)
    meta2 = _load_doc_metadata(uploads_dir, doc2_id)

    doc1 = Document(
        id=meta1["id"],
        name=meta1["name"],
        version=meta1.get("version", "1.0"),
        uploadDate=meta1.get("upload_date", ""),
        size=str(meta1.get("size_bytes", 0)),
        category=meta1.get("category", "application"),
    )
    doc2 = Document(
        id=meta2["id"],
        name=meta2["name"],
        version=meta2.get("version", "1.0"),
        uploadDate=meta2.get("upload_date", ""),
        size=str(meta2.get("size_bytes", 0)),
        category=meta2.get("category", "application"),
    )

    if engine_type == "real":
        from grc_policy_server.services.comparison.real_diff_engine import RealDiffEngine
        from grc_policy_server.services.llm.noop_llm import NoOpLLM
        from grc_policy_server.services.vector.qdrant_store import QdrantVectorClient

        qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
        qdrant = QdrantVectorClient(url=qdrant_url)
        engine = RealDiffEngine(
            qdrant=qdrant,
            neo4j=None,
            llm=NoOpLLM(),
            canonical_store=store,
        )
        print(f"  [engine=real, qdrant={qdrant_url}]")
    else:
        from grc_policy_server.services.comparison.offline_diff_engine import OfflineDiffEngine
        engine = OfflineDiffEngine(canonical_store=store)

    result = asyncio.run(
        engine.compare(
            doc1=doc1,
            doc2=doc2,
            audit_mode=True,
            save_to_db=False,
            testing_department=testing_department,
        )
    )

    # Save trace in the standard format
    trace_dir = uploads_dir / "_comparison_traces"
    trace_dir.mkdir(exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    trace_path = trace_dir / f"{doc1_id}__{doc2_id}__{ts}.json"

    trace_data = {
        "doc1Id": doc1_id,
        "doc2Id": doc2_id,
        "checkpoints": {
            "finalSummary": {
                "summary": result.summary,
                "referencedChangeRecordIds": [],
                "omittedChangeRecordIds": [],
                "followUpQuestions": result.followUpQuestions,
                "citationCoverage": {},
            },
            "diffRecords": {
                "changeCounts": _count_changes(result.keyDifferences),
                "numericChanges": _count_numeric(result.keyDifferences),
                "tableChanges": _count_tables(result.keyDifferences),
                "movedClauses": 0,
                "confidenceDistribution": [
                    d.severityConfidence for d in result.keyDifferences
                    if d.severityConfidence is not None
                ],
                "changeRecords": [d.model_dump(mode="json") for d in result.keyDifferences],
            },
            "alignmentResults": (result.accuracyMetrics.model_dump() if result.accuracyMetrics else {}),
        },
        "warnings": result.warnings,
    }
    trace_path.write_text(json.dumps(trace_data, indent=2, ensure_ascii=False))

    # Build summary
    diffs = result.keyDifferences
    change_counts = _count_changes(diffs)
    sev: dict[str, int] = {}
    for d in diffs:
        sev[d.changeSeverity] = sev.get(d.changeSeverity, 0) + 1

    accuracy = result.accuracyMetrics
    match_rate = None
    if accuracy:
        # Rough estimate: accuracy only has match info, unmatched derived from counts
        match_rate = round(
            accuracy.overall_confidence, 4
        ) if accuracy.overall_confidence else None

    return {
        "change_counts": change_counts,
        "severity": sev,
        "total_diffs": len(diffs),
        "table_changes": _count_tables(diffs),
        "warnings": result.warnings,
        "trace_path": str(trace_path),
        "match_rate": match_rate,
    }


def _count_changes(diffs) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in diffs:
        ct = d.changeType
        counts[ct] = counts.get(ct, 0) + 1
    return counts


def _count_numeric(diffs) -> int:
    return sum(1 for d in diffs if d.changes and any(
        c.type in ("modified",) for c in d.changes
        if hasattr(c, "type")
    ))


def _count_tables(diffs) -> int:
    return sum(1 for d in diffs if d.nodeType == "table")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run comparison pairs (offline or hybrid)")
    parser.add_argument(
        "--pairs",
        type=Path,
        default=Path("scripts/comparison_pairs_resolved.json"),
        help="Resolved pairs JSON (with doc IDs)",
    )
    parser.add_argument(
        "--uploads-dir", type=Path, default=Path("data/uploads")
    )
    parser.add_argument(
        "--engine",
        choices=["offline", "real"],
        default="offline",
        help=(
            "offline: OfflineDiffEngine (no external services, default). "
            "real: RealDiffEngine with Qdrant (requires QDRANT_URL env var)."
        ),
    )
    args = parser.parse_args()

    if not args.pairs.exists():
        print(
            f"ERROR: resolved pairs file not found: {args.pairs}\n"
            "Run scripts/ingest_docs.py first to populate doc IDs.",
            file=sys.stderr,
        )
        sys.exit(1)

    pairs = json.loads(args.pairs.read_text())["pairs"]
    print(f"Running {len(pairs)} comparison pairs from {args.pairs}\n")

    print(f"{'Pair':<40} {'Changes':>8} {'H/M/L':>12} {'Tables':>7} {'Warnings':>10}")
    print("-" * 82)

    for p in pairs:
        doc1_id = p.get("doc1_id")
        doc2_id = p.get("doc2_id")
        name = p.get("name", "?")

        if not doc1_id or not doc2_id:
            print(f"{name:<40} {'SKIP - missing doc IDs':>40}")
            continue

        try:
            result = run_comparison(
                doc1_id=doc1_id,
                doc2_id=doc2_id,
                testing_department=p.get("testing_department", ""),
                uploads_dir=args.uploads_dir,
                engine_type=args.engine,
            )
            total = result["total_diffs"]
            sev = result["severity"]
            h = sev.get("high", 0)
            m = sev.get("medium", 0)
            lo = sev.get("low", 0)
            tables = result["table_changes"]
            warn = len(result.get("warnings") or [])
            print(
                f"{name:<40} {total:>8} {h:>4}H/{m:>2}M/{lo:>2}L {tables:>7} {warn:>10}"
            )
            if result.get("warnings"):
                for w in result["warnings"]:
                    print(f"  ⚠ {w[:100]}")
        except FileNotFoundError:
            print(f"{name:<40} {'SKIP - docs not uploaded':>40}")
        except Exception as e:
            print(f"{name:<40} {'ERROR':>8}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\nTraces saved to {args.uploads_dir / '_comparison_traces'}/")


if __name__ == "__main__":
    main()
