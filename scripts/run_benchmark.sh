#!/usr/bin/env bash
# One-command benchmark iteration: archive → (optionally reingest) → compare → metrics
#
# Usage:
#   ./scripts/run_benchmark.sh                   # archive + compare + metrics (no reingest)
#   ./scripts/run_benchmark.sh --reingest        # full cycle with reingestion
#   API_URL=http://myhost:8000 ./scripts/run_benchmark.sh --reingest
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
API_URL="${API_URL:-http://localhost:8000}"

echo "============================================"
echo "  GRC Policy Server — Benchmark Pipeline"
echo "============================================"

echo ""
echo "=== Step 1: Archive current uploads ==="
uv run python "$SCRIPT_DIR/archive_uploads.py" \
  --uploads-dir "$PROJECT_ROOT/data/uploads" \
  --archive-root "$PROJECT_ROOT/data/archives"

if [[ "${1:-}" == "--reingest" ]]; then
  echo ""
  echo "=== Step 2: Re-ingest source documents ==="
  if [[ ! -d "$PROJECT_ROOT/data/source_docs" ]]; then
    echo "ERROR: data/source_docs/ not found. Populate it with source PDFs first." >&2
    exit 1
  fi
  uv run python "$SCRIPT_DIR/ingest_docs.py" \
    --source-dir "$PROJECT_ROOT/data/source_docs" \
    --api-url "$API_URL" \
    --uploads-dir "$PROJECT_ROOT/data/uploads" \
    --pairs-file "$SCRIPT_DIR/comparison_pairs.json" \
    --clear-existing
else
  echo ""
  echo "=== Step 2: Skipped (no --reingest flag) ==="
  echo "  Using existing uploads (re-running comparisons on current data)"
fi

echo ""
echo "=== Step 3: Run offline comparisons ==="
# Use resolved pairs if available, fall back to static
PAIRS_FILE="$SCRIPT_DIR/comparison_pairs_resolved.json"
if [[ ! -f "$PAIRS_FILE" ]]; then
  PAIRS_FILE="$SCRIPT_DIR/comparison_pairs.json"
  echo "  Note: using static pairs (no doc IDs resolved). Comparison may fail."
fi
uv run python "$SCRIPT_DIR/run_comparisons.py" \
  --pairs "$PAIRS_FILE" \
  --uploads-dir "$PROJECT_ROOT/data/uploads"

echo ""
echo "=== Step 4: Compute benchmark metrics ==="
TS=$(date -u +%Y%m%d_%H%M%S)
OUTPUT="$PROJECT_ROOT/data/benchmark_results/${TS}_metrics.json"
mkdir -p "$PROJECT_ROOT/data/benchmark_results"
uv run python "$SCRIPT_DIR/benchmark_metrics.py" \
  --uploads-dir "$PROJECT_ROOT/data/uploads" \
  --output "$OUTPUT" \
  --benchmark-doc "$PROJECT_ROOT/docs/benchmark_evaluation.md"

echo ""
echo "============================================"
echo "  Done."
echo "  Metrics : $OUTPUT"
echo "  Compare runs with:"
echo "    uv run python $SCRIPT_DIR/compare_baselines.py <before.json> $OUTPUT"
echo "============================================"
