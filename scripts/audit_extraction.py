#!/usr/bin/env python3
"""Extraction quality audit script.

Reads canonical_nodes.json (and raw_opd.json / raw_docling.json) from all
document directories under data/uploads/ and reports quality metrics.

Usage:
    uv run python scripts/audit_extraction.py               # all documents
    uv run python scripts/audit_extraction.py <doc_id>      # single document
    uv run python scripts/audit_extraction.py --json        # machine-readable
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

UPLOADS = Path("data/uploads")

# Canonical node_type values from hierarchy_builder
CONTENT_TYPES = {"paragraph", "list_item", "table"}
SECTION_TYPES = {"section"}
TABLE_TYPES = {"table"}
FIGURE_TYPES = {"figure"}


def _read_json(path: Path) -> list | dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def audit_document(doc_dir: Path) -> dict:
    nodes_file = doc_dir / "canonical_nodes.json"
    payload = _read_json(nodes_file)
    if not payload or not isinstance(payload, dict):
        return {"doc_id": doc_dir.name, "error": "canonical_nodes.json missing or invalid"}

    nodes: list[dict] = payload.get("nodes") or []

    by_type: Counter = Counter(n.get("node_type", "unknown") for n in nodes)

    # Content nodes: paragraph, list_item, table (sections are structural, not content)
    content_nodes = [n for n in nodes if n.get("node_type") in CONTENT_TYPES]
    # heading_path is a list of ancestor section titles; non-empty = has section context
    with_section = [
        n for n in content_nodes
        if isinstance(n.get("heading_path"), list) and len(n.get("heading_path") or []) > 0
    ]
    section_cov = len(with_section) / max(len(content_nodes), 1)

    # Subsection depth: length of heading_path list
    depths = [
        len(n.get("heading_path") or [])
        for n in content_nodes
        if isinstance(n.get("heading_path"), list) and len(n.get("heading_path") or []) > 0
    ]
    avg_depth = sum(depths) / max(len(depths), 1)
    max_depth = max(depths, default=0)

    # Unique section paths
    unique_sections = len({
        tuple(n.get("heading_path") or [])
        for n in content_nodes
        if n.get("heading_path")
    })

    # Table quality — table_headers are in node["metadata"]["table_headers"]
    tables = [n for n in nodes if n.get("node_type") in TABLE_TYPES]
    tables_ok = []
    tables_placeholder = []
    for t in tables:
        meta = t.get("metadata") or {}
        headers = meta.get("table_headers") or []
        if any(str(h).startswith("column_") for h in headers):
            tables_placeholder.append(t)
        else:
            tables_ok.append(t)

    tables_enhanced = [
        t for t in tables
        if (t.get("metadata") or {}).get("table_enhanced_by") == "pdfplumber"
    ]
    table_quality_pct = len(tables_ok) / max(len(tables), 1) * 100
    enhancement_pct = len(tables_enhanced) / max(len(tables), 1) * 100

    # Cross-page tables: page_from != page_to
    stitched = [
        t for t in tables
        if (t.get("page_from") or 0) != (t.get("page_to") or t.get("page_from") or 0)
    ]

    # Figure nodes
    figures = [n for n in nodes if n.get("node_type") in FIGURE_TYPES]
    figures_with_caption = [f for f in figures if (f.get("raw_text") or "").strip()]

    # Empty text nodes
    empty_nodes = [n for n in nodes if not (n.get("raw_text") or "").strip()]

    # Determine extractor source
    opd_file = doc_dir / "raw_opd.json"
    docling_file = doc_dir / "raw_docling.json"
    opd_data = _read_json(opd_file)
    docling_data = _read_json(docling_file)

    if opd_data and isinstance(opd_data, list):
        extractor = "opendataloader"
        raw_element_count = len(opd_data)
    elif isinstance(docling_data, dict) and docling_data.get("schema_name"):
        extractor = "docling"
        raw_element_count = "n/a"
    else:
        # Fallback: check metadata in canonical payload
        meta = payload.get("metadata") or {}
        extractor = str(meta.get("source") or "unknown")
        raw_element_count = "n/a"

    filename = payload.get("filename", doc_dir.name)

    return {
        "doc_id": doc_dir.name,
        "filename": filename,
        "extractor": extractor,
        "raw_element_count": raw_element_count,
        "total_nodes": len(nodes),
        "by_type": dict(by_type),
        "content_nodes": len(content_nodes),
        "section_coverage_pct": round(section_cov * 100, 1),
        "avg_section_depth": round(avg_depth, 2),
        "max_section_depth": max_depth,
        "unique_sections": unique_sections,
        "tables_total": len(tables),
        "tables_ok": len(tables_ok),
        "tables_placeholder_headers": len(tables_placeholder),
        "table_quality_pct": round(table_quality_pct, 1),
        "tables_enhanced_by_pdfplumber": len(tables_enhanced),
        "table_enhancement_pct": round(enhancement_pct, 1),
        "tables_cross_page": len(stitched),
        "figures_total": len(figures),
        "figures_with_caption": len(figures_with_caption),
        "empty_nodes": len(empty_nodes),
    }


def print_details(r: dict) -> None:
    if "error" in r:
        print(f"ERROR [{r['doc_id']}]: {r['error']}")
        return
    print(f"\n{'=' * 60}")
    print(f"Document: {r['filename']}")
    print(f"Doc ID:   {r['doc_id']}")
    print(f"Extractor: {r['extractor']}  (raw elements: {r['raw_element_count']})")
    print(f"\nNode counts:")
    for k, v in sorted(r["by_type"].items()):
        print(f"  {k:<15} {v:>5}")
    print(f"\nSection coverage:  {r['section_coverage_pct']}% of content nodes have a section path")
    print(f"Avg section depth: {r['avg_section_depth']} (max: {r['max_section_depth']})")
    print(f"Unique sections:   {r['unique_sections']}")
    print(f"\nTable quality:     {r['tables_ok']}/{r['tables_total']} OK ({r['table_quality_pct']}%)")
    print(f"  Placeholder col_ headers: {r['tables_placeholder_headers']}")
    print(f"  Enhanced by pdfplumber:   {r['tables_enhanced_by_pdfplumber']} ({r['table_enhancement_pct']}%)")
    print(f"  Cross-page span:          {r['tables_cross_page']}")
    print(f"\nFigures: {r['figures_total']} total, {r['figures_with_caption']} with caption")
    print(f"Empty nodes: {r['empty_nodes']}")


def print_summary(results: list[dict], *, as_json: bool = False, verbose: bool = False) -> None:
    if as_json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    valid = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]

    if errors:
        print("=== ERRORS ===")
        for r in errors:
            print(f"  {r['doc_id']}: {r['error']}")
        print()

    if not valid:
        print("No valid documents found.")
        return

    if verbose or len(valid) == 1:
        for r in valid:
            print_details(r)
        return

    col_w = 28
    print(
        f"{'Filename':<{col_w}} {'Ext':<12} {'Para':>5} {'Tables':>7} "
        f"{'Tbl-OK%':>8} {'Tbl-col_N':>10} {'Sect-cov%':>10} {'AvgDepth':>9} {'Empty':>6}"
    )
    print("-" * 110)
    for r in valid:
        fname = r["filename"]
        if len(fname) > col_w:
            fname = "…" + fname[-(col_w - 1):]
        print(
            f"{fname:<{col_w}} {r['extractor']:<12} "
            f"{r['by_type'].get('paragraph', 0):>5} "
            f"{r['tables_total']:>7} "
            f"{r['table_quality_pct']:>7.1f}% "
            f"{r['tables_placeholder_headers']:>10} "
            f"{r['section_coverage_pct']:>9.1f}% "
            f"{r['avg_section_depth']:>9.2f} "
            f"{r['empty_nodes']:>6}"
        )

    if len(valid) > 1:
        print()
        print("=== AGGREGATES ===")
        avg_tq = sum(r["table_quality_pct"] for r in valid) / len(valid)
        avg_sc = sum(r["section_coverage_pct"] for r in valid) / len(valid)
        total_enhanced = sum(r["tables_enhanced_by_pdfplumber"] for r in valid)
        total_tables = sum(r["tables_total"] for r in valid)
        total_placeholder = sum(r["tables_placeholder_headers"] for r in valid)
        print(f"  Avg table quality:     {avg_tq:.1f}%  ({total_placeholder} tables still have column_N headers)")
        print(f"  Avg section coverage:  {avg_sc:.1f}%")
        print(
            f"  pdfplumber enhanced:   {total_enhanced}/{total_tables} "
            f"({total_enhanced / max(total_tables, 1) * 100:.1f}%)"
        )


def main() -> None:
    args = sys.argv[1:]
    as_json = "--json" in args
    verbose = "--verbose" in args or "-v" in args
    args = [a for a in args if a not in {"--json", "--verbose", "-v"}]

    if not UPLOADS.exists():
        print(f"Uploads directory not found: {UPLOADS}", file=sys.stderr)
        print("Run from the project root directory.", file=sys.stderr)
        sys.exit(1)

    if args:
        doc_dirs = []
        for arg in args:
            candidate = UPLOADS / arg
            if candidate.is_dir():
                doc_dirs.append(candidate)
            else:
                matches = [d for d in UPLOADS.iterdir() if d.is_dir() and arg in d.name]
                doc_dirs.extend(sorted(matches, key=lambda d: d.name))
        if not doc_dirs:
            print(f"No document directories found for: {args}", file=sys.stderr)
            sys.exit(1)
    else:
        doc_dirs = sorted(
            (d for d in UPLOADS.iterdir() if d.is_dir() and (d / "canonical_nodes.json").exists()),
            key=lambda d: d.stat().st_mtime,
        )

    results = [audit_document(d) for d in doc_dirs]
    print_summary(results, as_json=as_json, verbose=verbose)


if __name__ == "__main__":
    main()
