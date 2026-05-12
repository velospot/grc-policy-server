"""Extraction validation service — reads existing canonical_nodes.json files from
data/uploads and computes extraction quality metrics without re-ingesting documents.

Usage:
    python -m grc_policy_server.services.ingestion.revalidation_runner
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExtractionMetrics:
    """Quality metrics computed from a single document's canonical_nodes.json."""

    document_id: str
    filename: str
    total_nodes: int = 0
    total_tables: int = 0
    total_clauses: int = 0
    tables_with_headers: int = 0
    tables_multi_page_stitched: int = 0
    ocr_node_count: int = 0
    avg_table_col_count: float = 0.0
    degenerate_tables_filtered: int = 0
    # Accuracy estimates
    section_coverage_pct: float = 0.0   # % nodes with non-empty heading_path
    header_quality_pct: float = 0.0     # % tables with no column_N fallback headers
    reference_section_filter_pct: float = 0.0  # % tables excluded (lower = better)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.filename} | nodes={self.total_nodes} tables={self.total_tables} "
            f"clauses={self.total_clauses} stitched={self.tables_multi_page_stitched} "
            f"ocr={self.ocr_node_count} hdr_qual={self.header_quality_pct:.0%} "
            f"sec_cov={self.section_coverage_pct:.0%}"
        )


class ExtractionValidator:
    """Validates extraction quality by reading canonical_nodes.json from data/uploads."""

    def __init__(self, upload_root: Path) -> None:
        self.upload_root = upload_root

    def validate_document(self, document_id: str) -> ExtractionMetrics:
        """Load canonical_nodes.json from document directory and compute metrics."""
        doc_dir = self.upload_root / document_id
        nodes_file = doc_dir / "canonical_nodes.json"
        metadata_file = doc_dir / "metadata.json"

        metrics = ExtractionMetrics(document_id=document_id, filename=document_id)

        # Load filename from metadata if available
        if metadata_file.exists():
            try:
                meta = json.loads(metadata_file.read_text(encoding="utf-8"))
                metrics.filename = meta.get("filename") or meta.get("original_filename") or document_id
            except Exception as e:
                metrics.errors.append(f"metadata read error: {e}")

        if not nodes_file.exists():
            metrics.errors.append(f"canonical_nodes.json not found in {doc_dir}")
            return metrics

        try:
            raw = json.loads(nodes_file.read_text(encoding="utf-8"))
            nodes = raw if isinstance(raw, list) else (raw.get("nodes") or [])
        except Exception as e:
            metrics.errors.append(f"nodes load error: {e}")
            return metrics

        metrics.total_nodes = len(nodes)

        col_counts: list[int] = []
        tables_with_fallback_headers = 0

        for node in nodes:
            node_type = str(node.get("node_type") or "").lower()
            heading_path = node.get("heading_path") or []
            meta = node.get("metadata") or {}

            if heading_path:
                pass  # section_coverage counts below

            if node_type == "table":
                metrics.total_tables += 1
                table_struct = meta.get("table_structure") or {}
                num_cols = int(table_struct.get("num_cols") or 0)
                if num_cols > 0:
                    col_counts.append(num_cols)

                # Check header quality: any column_N fallback headers present?
                cells = table_struct.get("cells") or []
                header_cells = [c for c in cells if c.get("is_header")]
                has_fallback = any(
                    str(c.get("text") or "").lower().startswith("column_")
                    for c in header_cells
                )
                if has_fallback:
                    tables_with_fallback_headers += 1
                else:
                    metrics.tables_with_headers += 1

                # Multi-page stitched
                if meta.get("page_split_merged"):
                    metrics.tables_multi_page_stitched += 1

                # Degenerate: 1 column with >50 chars text in all cells
                if num_cols == 1 and cells:
                    avg_len = sum(len(str(c.get("text") or "")) for c in cells) / max(len(cells), 1)
                    if avg_len > 50:
                        metrics.degenerate_tables_filtered += 1

            elif node_type in {"clause", "paragraph", "list_item", "note", "warning", "definition"}:
                metrics.total_clauses += 1

            if node.get("ocr_used") or meta.get("ocr_used"):
                metrics.ocr_node_count += 1

        # Compute derived metrics
        if metrics.total_nodes > 0:
            nodes_with_path = sum(
                1 for n in nodes
                if n.get("heading_path") or n.get("metadata", {}).get("section_path")
            )
            metrics.section_coverage_pct = nodes_with_path / metrics.total_nodes

        if metrics.total_tables > 0:
            metrics.header_quality_pct = metrics.tables_with_headers / metrics.total_tables
            metrics.reference_section_filter_pct = metrics.degenerate_tables_filtered / metrics.total_tables

        metrics.avg_table_col_count = sum(col_counts) / len(col_counts) if col_counts else 0.0

        return metrics

    def compare_before_after(
        self,
        before_nodes: list[dict[str, Any]],
        after_nodes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compare two node lists (before/after extraction improvement) and report delta."""
        def _count(nodes: list[dict], node_type: str) -> int:
            return sum(1 for n in nodes if str(n.get("node_type") or "").lower() == node_type)

        def _stitched(nodes: list[dict]) -> int:
            return sum(1 for n in nodes if (n.get("metadata") or {}).get("page_split_merged"))

        return {
            "total_nodes_before": len(before_nodes),
            "total_nodes_after": len(after_nodes),
            "tables_before": _count(before_nodes, "table"),
            "tables_after": _count(after_nodes, "table"),
            "stitched_tables_before": _stitched(before_nodes),
            "stitched_tables_after": _stitched(after_nodes),
            "delta_tables": _count(after_nodes, "table") - _count(before_nodes, "table"),
            "delta_stitched": _stitched(after_nodes) - _stitched(before_nodes),
        }

    def validate_all_uploads(self) -> list[ExtractionMetrics]:
        """Run validate_document on every subdirectory in upload_root."""
        if not self.upload_root.exists():
            logger.warning("Upload root does not exist: %s", self.upload_root)
            return []

        results: list[ExtractionMetrics] = []
        for item in sorted(self.upload_root.iterdir()):
            if not item.is_dir() or item.name.startswith("_"):
                continue
            nodes_file = item / "canonical_nodes.json"
            if not nodes_file.exists():
                continue
            logger.info("Validating document: %s", item.name)
            metrics = self.validate_document(item.name)
            results.append(metrics)

        return results

    def print_report(self, metrics: list[ExtractionMetrics]) -> None:
        """Print an ASCII summary table of extraction metrics."""
        if not metrics:
            print("No documents to validate.")
            return

        print("\n" + "=" * 100)
        print("EXTRACTION VALIDATION REPORT")
        print("=" * 100)
        header = (
            f"{'Document':<40} {'Nodes':>6} {'Tables':>7} {'Stitched':>9} "
            f"{'OCR':>5} {'HdrQual':>8} {'SecCov':>7} {'Errors':>6}"
        )
        print(header)
        print("-" * 100)
        for m in metrics:
            name = m.filename[:38]
            err_count = len(m.errors)
            print(
                f"{name:<40} {m.total_nodes:>6} {m.total_tables:>7} "
                f"{m.tables_multi_page_stitched:>9} {m.ocr_node_count:>5} "
                f"{m.header_quality_pct:>7.0%} {m.section_coverage_pct:>6.0%} "
                f"{err_count:>6}"
            )
            for err in m.errors:
                print(f"  ! {err}")
        print("=" * 100)
