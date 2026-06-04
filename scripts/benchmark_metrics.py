"""Compute benchmark metrics from hierarchy files and comparison traces.

Outputs a JSON metrics file and appends an iteration row to docs/benchmark_evaluation.md.

Usage:
    uv run python scripts/benchmark_metrics.py
    uv run python scripts/benchmark_metrics.py --uploads-dir data/uploads --output data/benchmark_results/metrics.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Document metrics (from hierarchy.json)
# ---------------------------------------------------------------------------

def _doc_metrics(doc_dir: Path) -> dict | None:
    hier_path = doc_dir / "hierarchy.json"
    meta_path = doc_dir / "metadata.json"
    if not hier_path.exists() or not meta_path.exists():
        return None

    meta = json.loads(meta_path.read_text())
    hier = json.loads(hier_path.read_text())
    nodes = hier.get("nodes", [])

    node_counts: dict[str, int] = {}
    for n in nodes:
        nt = n.get("node_type", "unknown")
        node_counts[nt] = node_counts.get(nt, 0) + 1

    # Section hierarchy depth (max len of section_titles)
    max_depth = 0
    unsectioned = 0
    for n in nodes:
        st = n.get("section_titles") or []
        if st:
            max_depth = max(max_depth, len(st))
        sp = n.get("section_path") or ""
        if sp in ("Unsectioned", "", "Unknown Section"):
            unsectioned += 1

    # Table quality from metadata
    table_nodes = [n for n in nodes if n.get("node_type") == "table"]
    table_fills: list[float] = []
    table_numeric: list[float] = []
    stub_header_count = 0
    camelot_count = 0

    for t in table_nodes:
        tmeta = t.get("metadata") or {}
        ts_data = tmeta.get("table_structure") or {}
        cells = ts_data.get("cells") or []
        nr = ts_data.get("num_rows") or 0
        nc = ts_data.get("num_cols") or 0
        total = nr * nc
        if total > 0:
            non_empty = sum(1 for c in cells if str(c.get("text", "")).strip())
            fill = non_empty / total
            numeric = sum(1 for c in cells if any(ch.isdigit() for ch in str(c.get("text", "")))) / max(1, non_empty) if non_empty else 0
            table_fills.append(fill)
            table_numeric.append(numeric)

        headers = tmeta.get("table_headers") or []
        if any(h.startswith("_row_label_") or h == "column_1" for h in headers):
            stub_header_count += 1
        if tmeta.get("table_source") in ("camelot", "ensemble"):
            camelot_count += 1

    quality_score = tmeta.get("extraction_quality_score") if table_nodes else None

    return {
        "id": meta.get("id"),
        "name": meta.get("name"),
        "family": meta.get("document_family"),
        "node_counts": node_counts,
        "section_hierarchy_max_depth": max_depth,
        "unsectioned_node_count": unsectioned,
        "tables": {
            "count": len(table_nodes),
            "avg_fill_rate": round(sum(table_fills) / len(table_fills), 4) if table_fills else None,
            "avg_numeric_density": round(sum(table_numeric) / len(table_numeric), 4) if table_numeric else None,
            "stub_header_count": stub_header_count,
            "camelot_or_ensemble_count": camelot_count,
        },
    }


# ---------------------------------------------------------------------------
# Comparison pair metrics (from latest trace for each pair)
# ---------------------------------------------------------------------------

def _latest_trace(trace_dir: Path, doc1_id: str, doc2_id: str) -> Path | None:
    candidates = sorted(
        trace_dir.glob(f"{doc1_id}__{doc2_id}__*.json"), reverse=True
    )
    return candidates[0] if candidates else None


def _pair_metrics(
    pair: dict,
    trace_dir: Path,
) -> dict:
    doc1_id = pair.get("doc1_id") or ""
    doc2_id = pair.get("doc2_id") or ""
    trace_path = _latest_trace(trace_dir, doc1_id, doc2_id)
    if not trace_path:
        return {
            "name": pair.get("name"),
            "doc1_id": doc1_id,
            "doc2_id": doc2_id,
            "error": "no trace found",
        }

    try:
        data = json.loads(trace_path.read_text())
    except Exception as e:
        return {"name": pair.get("name"), "error": str(e)}

    cp = data.get("checkpoints", {})
    align = cp.get("alignmentResults", {})
    dr = cp.get("diffRecords", {}) if isinstance(cp.get("diffRecords"), dict) else {}
    crs = dr.get("changeRecords", [])
    if not isinstance(crs, list):
        crs = []

    matched = align.get("matchedNodes") or align.get("total_matches") or 0
    ul = align.get("unmatchedLeft") or 0
    ur = align.get("unmatchedRight") or 0
    total = matched + ul + ur
    match_rate = round(matched / total, 4) if total else 0.0

    sev: dict[str, int] = {}
    table_records = 0
    empty_doc1_table_content = 0
    modified_table_close = 0

    for r in crs:
        s = r.get("changeSeverity", "unknown")
        sev[s] = sev.get(s, 0) + 1
        if r.get("nodeType") == "table":
            table_records += 1
            if r.get("changeType") == "MODIFIED":
                dist = r.get("distance")
                if isinstance(dist, (int, float)) and dist < 0.5:
                    modified_table_close += 1
                    if not str(r.get("doc1Content") or "").strip():
                        empty_doc1_table_content += 1

    conf_vals = []
    for c in dr.get("confidenceDistribution", []) or []:
        if isinstance(c, (int, float)):
            conf_vals.append(c)
        elif isinstance(c, dict):
            conf_vals.append(c.get("confidence", 0.0))

    warnings = data.get("warnings") or []

    return {
        "name": pair.get("name"),
        "family": pair.get("family"),
        "doc1_id": doc1_id,
        "doc2_id": doc2_id,
        "trace_file": trace_path.name,
        "matched": matched,
        "unmatched_left": ul,
        "unmatched_right": ur,
        "match_rate": match_rate,
        "change_counts": dr.get("changeCounts", {}),
        "severity_distribution": sev,
        "total_change_records": len(crs),
        "table_change_records": table_records,
        "empty_doc1content_rate_table_modified": (
            round(empty_doc1_table_content / modified_table_close, 4)
            if modified_table_close else None
        ),
        "compatibility_warning_issued": any(
            "low section-title overlap" in str(w).lower() or "different standards" in str(w).lower()
            for w in warnings
        ),
        "confidence_mean": round(sum(conf_vals) / len(conf_vals), 4) if conf_vals else None,
        "confidence_min": round(min(conf_vals), 4) if conf_vals else None,
        "numeric_changes": dr.get("numericChanges"),
        "table_changes": dr.get("tableChanges"),
        "expected_incompatible": pair.get("expected_incompatible", False),
    }


# ---------------------------------------------------------------------------
# Global summary
# ---------------------------------------------------------------------------

def _global_summary(doc_metrics_list: list[dict], pair_metrics_list: list[dict]) -> dict:
    in_family = [
        p for p in pair_metrics_list
        if not p.get("expected_incompatible") and p.get("match_rate") is not None
    ]
    avg_match = (
        round(sum(p["match_rate"] for p in in_family) / len(in_family), 4)
        if in_family else None
    )
    camelot_total = sum(
        d.get("tables", {}).get("camelot_or_ensemble_count", 0)
        for d in doc_metrics_list
        if isinstance(d, dict)
    )
    stub_total = sum(
        d.get("tables", {}).get("stub_header_count", 0)
        for d in doc_metrics_list
        if isinstance(d, dict)
    )
    max_hier = max(
        (d.get("section_hierarchy_max_depth", 0) for d in doc_metrics_list if isinstance(d, dict)),
        default=0,
    )
    return {
        "avg_match_rate_in_family": avg_match,
        "camelot_upgrade_count": camelot_total,
        "stub_header_count_total": stub_total,
        "max_section_hierarchy_depth": max_hier,
    }


# ---------------------------------------------------------------------------
# Markdown update
# ---------------------------------------------------------------------------

_ITER_TABLE_HEADER = """\
## 11. Iteration History

| Timestamp | Git | Avg Match | TL p013 | DNVGL p023 | Camelot | Stub Headers | Max Depth |
|---|---|---|---|---|---|---|---|"""


def _append_iteration_row(md_path: Path, metrics: dict) -> None:
    ts = metrics.get("run_timestamp", "?")[:16]
    commit = (metrics.get("git_commit") or "?")[:7]
    s = metrics.get("summary", {})
    avg = f"{s.get('avg_match_rate_in_family', 0)*100:.1f}%" if s.get("avg_match_rate_in_family") is not None else "?"
    camelot = s.get("camelot_upgrade_count", 0)
    stub = s.get("stub_header_count_total", "?")
    depth = s.get("max_section_hierarchy_depth", "?")

    tl_p013 = next(
        (f"{p['match_rate']*100:.1f}%" for p in metrics.get("comparison_pairs", [])
         if "p013" in p.get("name", "") and "TL" in p.get("name", "") and p.get("match_rate") is not None),
        "?",
    )
    dnvgl_p023 = next(
        (f"{p['match_rate']*100:.1f}%" for p in metrics.get("comparison_pairs", [])
         if "p023" in p.get("name", "") and p.get("match_rate") is not None),
        "?",
    )

    new_row = f"| {ts} | {commit} | {avg} | {tl_p013} | {dnvgl_p023} | {camelot} | {stub} | {depth} |"

    if not md_path.exists():
        return

    content = md_path.read_text()
    if "## 11. Iteration History" not in content:
        content = content.rstrip() + "\n\n---\n\n" + _ITER_TABLE_HEADER + "\n"

    # Append the row before the end of the section (or end of file)
    if new_row not in content:
        # Find end of table: insert before next ## or end of file
        import re
        pattern = r"(\| Timestamp.*?\|(?:\n\|[^\n]*\|)*)"
        m = re.search(pattern, content, re.DOTALL)
        if m:
            updated = content[: m.end()] + "\n" + new_row + content[m.end():]
        else:
            updated = content + "\n" + new_row
        md_path.write_text(updated)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def compute_metrics(uploads_dir: Path) -> dict:
    doc_metrics_list = []
    for doc_dir in sorted(uploads_dir.iterdir()):
        if doc_dir.name.startswith("_") or not doc_dir.is_dir():
            continue
        dm = _doc_metrics(doc_dir)
        if dm:
            doc_metrics_list.append(dm)

    # Load resolved pairs
    pairs_path = Path("scripts/comparison_pairs_resolved.json")
    pairs = []
    if pairs_path.exists():
        pairs = json.loads(pairs_path.read_text()).get("pairs", [])
    else:
        # Fall back to static pairs with id lookup
        static_path = Path("scripts/comparison_pairs.json")
        if static_path.exists():
            static_pairs = json.loads(static_path.read_text()).get("pairs", [])
            # Try to resolve from current uploads
            fname_to_id: dict[str, str] = {}
            for doc in doc_metrics_list:
                fname_to_id[doc["name"]] = doc["id"]
            for p in static_pairs:
                pairs.append({
                    **p,
                    "doc1_id": fname_to_id.get(p["doc1_filename"]),
                    "doc2_id": fname_to_id.get(p["doc2_filename"]),
                })

    trace_dir = uploads_dir / "_comparison_traces"
    pair_metrics_list = []
    for p in pairs:
        if p.get("doc1_id") and p.get("doc2_id"):
            pair_metrics_list.append(_pair_metrics(p, trace_dir))

    summary = _global_summary(doc_metrics_list, pair_metrics_list)

    def _git_commit() -> str:
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], text=True
            ).strip()
        except Exception:
            return "unknown"

    return {
        "run_timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "documents": doc_metrics_list,
        "comparison_pairs": pair_metrics_list,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute benchmark metrics")
    parser.add_argument("--uploads-dir", type=Path, default=Path("data/uploads"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--benchmark-doc",
        type=Path,
        default=Path("docs/benchmark_evaluation.md"),
        help="Benchmark markdown doc to update with iteration row",
    )
    args = parser.parse_args()

    if not args.uploads_dir.is_dir():
        print(f"ERROR: uploads dir not found: {args.uploads_dir}", file=sys.stderr)
        sys.exit(1)

    metrics = compute_metrics(args.uploads_dir)

    # Determine output path
    if args.output:
        out_path = args.output
    else:
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_dir = Path("data/benchmark_results")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{ts}_metrics.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False))

    # Update benchmark markdown
    if args.benchmark_doc.exists():
        _append_iteration_row(args.benchmark_doc, metrics)
        print(f"Benchmark doc updated: {args.benchmark_doc}")

    # Print summary
    s = metrics["summary"]
    print(f"\n=== Benchmark Metrics ===")
    print(f"Git commit      : {metrics['git_commit']}")
    print(f"Documents       : {len(metrics['documents'])}")
    if s.get("avg_match_rate_in_family") is not None:
        print(f"Avg match rate  : {s['avg_match_rate_in_family']:.1%} (in-family pairs)")
    print(f"Max hier depth  : {s.get('max_section_hierarchy_depth', '?')}")
    print(f"Camelot upgrades: {s.get('camelot_upgrade_count', 0)}")
    print(f"Stub headers    : {s.get('stub_header_count_total', '?')}")
    print()

    for p in metrics["comparison_pairs"]:
        mr = p.get("match_rate")
        mr_s = f"{mr:.1%}" if mr is not None else "?"
        cc = p.get("change_counts") or {}
        warn = "⚠ incompatible" if p.get("compatibility_warning_issued") else ""
        print(f"  {p['name']:<40} match={mr_s:>7}  {cc}  {warn}")

    print(f"\nMetrics saved: {out_path}")


if __name__ == "__main__":
    main()
