"""Standalone revalidation runner — validates all documents in data/uploads.

Run with:
    python -m grc_policy_server.services.ingestion.revalidation_runner

Reads existing canonical_nodes.json files (no re-ingestion needed) and writes
a JSON report to data/uploads/_validation_report.json.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    from grc_policy_server.core.config import settings
    from grc_policy_server.services.ingestion.extraction_validator import ExtractionValidator
    from grc_policy_server.services.ingestion.accuracy_evaluator import AccuracyEvaluator

    upload_root = Path(settings.upload_root)

    # ── Structural validation ────────────────────────────────────────────────
    validator = ExtractionValidator(upload_root)
    logger.info("Scanning %s for documents ...", upload_root)
    metrics = validator.validate_all_uploads()

    if not metrics:
        logger.warning("No documents found to validate.")
        return

    validator.print_report(metrics)

    report_data = [
        {
            "document_id": m.document_id,
            "filename": m.filename,
            "total_nodes": m.total_nodes,
            "total_tables": m.total_tables,
            "total_clauses": m.total_clauses,
            "tables_with_headers": m.tables_with_headers,
            "tables_multi_page_stitched": m.tables_multi_page_stitched,
            "ocr_node_count": m.ocr_node_count,
            "avg_table_col_count": m.avg_table_col_count,
            "degenerate_tables_filtered": m.degenerate_tables_filtered,
            "section_coverage_pct": m.section_coverage_pct,
            "header_quality_pct": m.header_quality_pct,
            "reference_section_filter_pct": m.reference_section_filter_pct,
            "raw_docling_table_count": m.raw_docling_table_count,
            "lost_tables_count": m.lost_tables_count,
            "lost_table_pages": m.lost_table_pages,
            "errors": m.errors,
        }
        for m in metrics
    ]

    # ── Deep accuracy evaluation ─────────────────────────────────────────────
    logger.info("Running deep accuracy evaluation ...")
    evaluator = AccuracyEvaluator(upload_root)
    accuracy_results = evaluator.evaluate_all()
    evaluator.print_report(accuracy_results)

    accuracy_data = [
        {
            "document_id": dm.document_id,
            "filename": dm.filename,
            "total_tables": dm.total_tables,
            "tables_classified": dm.tables_classified,
            "emc_type_distribution": dm.emc_type_distribution,
            "overall_fact_coverage": dm.overall_fact_coverage,
            "overall_row_key_coverage": dm.overall_row_key_coverage,
            "overall_column_mapped_pct": dm.overall_column_mapped_pct,
            "total_normalized_facts": dm.total_normalized_facts,
            "errors": dm.errors,
            "tables": [
                {
                    "index": tm.table_index,
                    "caption": tm.caption,
                    "emc_test_type": tm.emc_test_type,
                    "headers": tm.headers,
                    "header_entity_types": tm.header_entity_types,
                    "column_mapped_pct": tm.column_mapped_pct,
                    "fact_bearing_cells_pct": tm.fact_bearing_cells_pct,
                    "row_key_coverage_pct": tm.row_key_coverage_pct,
                    "fact_type_counts": tm.fact_type_counts,
                    "sample_row_keys": tm.sample_row_keys,
                }
                for tm in dm.table_metrics
            ],
        }
        for dm in accuracy_results
    ]

    # ── Write reports ────────────────────────────────────────────────────────
    report_path = upload_root / "_validation_report.json"
    accuracy_path = upload_root / "_accuracy_report.json"
    try:
        report_path.write_text(json.dumps(report_data, indent=2, ensure_ascii=False))
        logger.info("Validation report written to %s", report_path)
        accuracy_path.write_text(json.dumps(accuracy_data, indent=2, ensure_ascii=False))
        logger.info("Accuracy report written to %s", accuracy_path)
    except Exception as e:
        logger.error("Failed to write report: %s", e)


if __name__ == "__main__":
    asyncio.run(main())
