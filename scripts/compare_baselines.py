"""Compare two benchmark metrics JSON files and show improvement/regression.

Usage:
    uv run python scripts/compare_baselines.py data/benchmark_results/BEFORE.json data/benchmark_results/AFTER.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _pct(v: float | None) -> str:
    return f"{v * 100:.1f}%" if v is not None else "N/A"


def _delta(before: float | None, after: float | None, higher_is_better: bool = True) -> str:
    if before is None or after is None:
        return "N/A"
    diff = after - before
    sign = "+" if diff >= 0 else ""
    arrow = ("↑" if diff > 0 else "↓") if diff != 0 else "="
    colour = ""
    if higher_is_better:
        colour = "✓" if diff > 0 else ("✗" if diff < 0 else "")
    else:
        colour = "✓" if diff < 0 else ("✗" if diff > 0 else "")
    return f"{sign}{diff * 100:.1f}% {arrow} {colour}"


def _int_delta(before: int | None, after: int | None, lower_is_better: bool = False) -> str:
    if before is None or after is None:
        return "N/A"
    diff = after - before
    sign = "+" if diff >= 0 else ""
    arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "=")
    if lower_is_better:
        colour = "✓" if diff < 0 else ("✗" if diff > 0 else "")
    else:
        colour = "✓" if diff > 0 else ("✗" if diff < 0 else "")
    return f"{sign}{diff} {arrow} {colour}"


def compare(before_path: Path, after_path: Path) -> None:
    before = json.loads(before_path.read_text())
    after = json.loads(after_path.read_text())

    print(f"\n=== Baseline comparison ===")
    print(f"Before : {before.get('run_timestamp','?')[:19]}  commit={before.get('git_commit','?')}")
    print(f"After  : {after.get('run_timestamp','?')[:19]}  commit={after.get('git_commit','?')}")
    print()

    bsummary = before.get("summary", {})
    asummary = after.get("summary", {})

    # Summary metrics
    rows = [
        ("Avg match rate (in-family)", bsummary.get("avg_match_rate_in_family"), asummary.get("avg_match_rate_in_family"), True, "pct"),
        ("Max section hierarchy depth", bsummary.get("max_section_hierarchy_depth"), asummary.get("max_section_hierarchy_depth"), True, "int"),
        ("Camelot/ensemble upgrades", bsummary.get("camelot_upgrade_count", 0), asummary.get("camelot_upgrade_count", 0), True, "int"),
        ("Stub headers (col_1 etc.)", bsummary.get("stub_header_count_total", 0), asummary.get("stub_header_count_total", 0), False, "int_lower"),
    ]

    # Per-pair match rates
    bpairs = {p["name"]: p for p in before.get("comparison_pairs", [])}
    apairs = {p["name"]: p for p in after.get("comparison_pairs", [])}
    all_names = sorted(set(bpairs) | set(apairs))
    for n in all_names:
        bp = bpairs.get(n, {})
        ap = apairs.get(n, {})
        rows.append((f"  {n}", bp.get("match_rate"), ap.get("match_rate"), True, "pct"))

    # Per-pair table empty content rate
    for n in all_names:
        bp = bpairs.get(n, {})
        ap = apairs.get(n, {})
        bv = bp.get("empty_doc1content_rate_table_modified")
        av = ap.get("empty_doc1content_rate_table_modified")
        if bv is not None or av is not None:
            rows.append((f"  {n} table empty doc1Content", bv, av, False, "pct"))

    # Print table
    col_w = 45
    print(f"{'Metric':<{col_w}} {'Before':>10} {'After':>10}  Delta")
    print("-" * (col_w + 35))
    for label, bv, av, higher_is_better, fmt in rows:
        if fmt == "pct":
            bv_s = _pct(bv)
            av_s = _pct(av)
            d = _delta(bv, av, higher_is_better)
        elif fmt == "int":
            bv_s = str(bv) if bv is not None else "N/A"
            av_s = str(av) if av is not None else "N/A"
            d = _int_delta(bv, av, lower_is_better=False)
        elif fmt == "int_lower":
            bv_s = str(bv) if bv is not None else "N/A"
            av_s = str(av) if av is not None else "N/A"
            d = _int_delta(bv, av, lower_is_better=True)
        else:
            bv_s = str(bv)
            av_s = str(av)
            d = ""
        print(f"{label:<{col_w}} {bv_s:>10} {av_s:>10}  {d}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Diff two benchmark metrics JSON files")
    parser.add_argument("before", type=Path, help="Before metrics JSON")
    parser.add_argument("after", type=Path, help="After metrics JSON")
    args = parser.parse_args()

    for p in (args.before, args.after):
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            sys.exit(1)

    compare(args.before, args.after)


if __name__ == "__main__":
    main()
