"""Upload PDFs from a source directory to the running API.

After uploading, resolves comparison_pairs.json filenames to actual document IDs
and writes scripts/comparison_pairs_resolved.json for use by run_comparisons.py.

Usage:
    uv run python scripts/ingest_docs.py --source-dir data/source_docs --api-url http://localhost:8000
    uv run python scripts/ingest_docs.py --source-dir data/source_docs --api-url http://localhost:8000 --clear-existing
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# ---------------------------------------------------------------------------
# HTTP helpers (no third-party libs)
# ---------------------------------------------------------------------------

def _api_get(base_url: str, path: str) -> dict:
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _api_post_json(base_url: str, path: str, body: dict) -> dict:
    url = base_url.rstrip("/") + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _upload_file(base_url: str, pdf_path: Path) -> dict:
    """Multipart POST /documents/upload for a single PDF."""
    url = base_url.rstrip("/") + "/documents/upload"
    boundary = "----GRCBenchmarkBoundary"
    body_parts: list[bytes] = []
    body_parts.append(f'--{boundary}\r\n'.encode())
    body_parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{pdf_path.name}"\r\n'.encode()
    )
    body_parts.append(b'Content-Type: application/pdf\r\n\r\n')
    body_parts.append(pdf_path.read_bytes())
    body_parts.append(f'\r\n--{boundary}--\r\n'.encode())
    body = b"".join(body_parts)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        raise RuntimeError(f"HTTP {e.code}: {body_bytes.decode(errors='replace')}") from e


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def _existing_doc_ids(uploads_dir: Path) -> list[str]:
    ids = []
    for doc_dir in uploads_dir.iterdir():
        if doc_dir.name.startswith("_") or not doc_dir.is_dir():
            continue
        meta_path = doc_dir / "metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                if meta.get("id"):
                    ids.append(meta["id"])
            except Exception:
                pass
    return ids


def _resolve_pairs(
    pairs_path: Path, filename_to_id: dict[str, str]
) -> list[dict]:
    pairs = json.loads(pairs_path.read_text())["pairs"]
    resolved = []
    for p in pairs:
        d1 = filename_to_id.get(p["doc1_filename"])
        d2 = filename_to_id.get(p["doc2_filename"])
        resolved.append({**p, "doc1_id": d1, "doc2_id": d2})
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload PDFs to the running API")
    parser.add_argument("--source-dir", type=Path, default=Path("data/source_docs"))
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument(
        "--clear-existing",
        action="store_true",
        help="Delete all existing documents before uploading",
    )
    parser.add_argument("--uploads-dir", type=Path, default=Path("data/uploads"))
    parser.add_argument(
        "--pairs-file",
        type=Path,
        default=Path("scripts/comparison_pairs.json"),
    )
    args = parser.parse_args()

    if not args.source_dir.is_dir():
        print(f"ERROR: source dir not found: {args.source_dir}", file=sys.stderr)
        sys.exit(1)

    # Health check
    try:
        health = _api_get(args.api_url, "/health")
        print(f"API reachable: {health}")
    except Exception as e:
        print(f"ERROR: cannot reach API at {args.api_url}: {e}", file=sys.stderr)
        sys.exit(1)

    # Optional: clear existing
    if args.clear_existing and args.uploads_dir.is_dir():
        existing = _existing_doc_ids(args.uploads_dir)
        if existing:
            print(f"Deleting {len(existing)} existing documents …")
            try:
                resp = _api_post_json(
                    args.api_url, "/documents/delete", {"documentIds": existing}
                )
                print(f"  Deleted {resp.get('deletedCount', 0)} documents")
            except Exception as e:
                print(f"  Warning: delete failed: {e}")
            time.sleep(1)

    # Upload all PDFs
    pdfs = sorted(args.source_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {args.source_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"\nUploading {len(pdfs)} PDFs from {args.source_dir} …\n")
    filename_to_id: dict[str, str] = {}
    rows = []

    for pdf in pdfs:
        try:
            resp = _upload_file(args.api_url, pdf)
            results = resp.get("results", [])
            if results:
                r = results[0]
                doc_id = r.get("documentId") or "?"
                chunks = r.get("chunksStored") or 0
                error = r.get("error") or ""
                status = "OK" if r.get("accepted") else f"FAIL: {error}"
                filename_to_id[pdf.name] = doc_id
                rows.append((pdf.name, doc_id[:8] + "…", chunks, status))
            else:
                rows.append((pdf.name, "?", 0, "no results"))
        except Exception as e:
            rows.append((pdf.name, "ERROR", 0, str(e)[:60]))

    # Print table
    print(f"{'Filename':<45} {'Doc ID':<12} {'Chunks':>7}  Status")
    print("-" * 80)
    for fname, doc_id, chunks, status in rows:
        print(f"{fname:<45} {doc_id:<12} {chunks:>7}  {status}")

    # Resolve and write comparison pairs
    pairs_resolved_path = args.pairs_file.parent / "comparison_pairs_resolved.json"
    if args.pairs_file.exists():
        try:
            resolved = _resolve_pairs(args.pairs_file, filename_to_id)
            pairs_resolved_path.write_text(
                json.dumps({"pairs": resolved}, indent=2, ensure_ascii=False)
            )
            print(f"\nResolved pairs written to: {pairs_resolved_path}")
            missing = [p["name"] for p in resolved if not p.get("doc1_id") or not p.get("doc2_id")]
            if missing:
                print(f"  WARNING: unresolved pairs: {missing}")
        except Exception as e:
            print(f"  Warning: could not resolve pairs: {e}")


if __name__ == "__main__":
    main()
