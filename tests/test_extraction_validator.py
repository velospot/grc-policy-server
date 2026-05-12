"""Tests for Phase E extraction validator."""

from __future__ import annotations

import json
import pytest
from pathlib import Path


class TestExtractionValidator:
    def test_validate_document_missing_file(self, tmp_path):
        from grc_policy_server.services.ingestion.extraction_validator import ExtractionValidator

        validator = ExtractionValidator(tmp_path)
        doc_dir = tmp_path / "doc001"
        doc_dir.mkdir()
        metrics = validator.validate_document("doc001")
        assert metrics.document_id == "doc001"
        assert metrics.total_nodes == 0
        assert len(metrics.errors) > 0

    def test_validate_document_with_nodes(self, tmp_path):
        from grc_policy_server.services.ingestion.extraction_validator import ExtractionValidator

        doc_dir = tmp_path / "doc002"
        doc_dir.mkdir()
        nodes = [
            {"node_type": "paragraph", "heading_path": ["Section 1"], "metadata": {}},
            {"node_type": "table", "heading_path": ["Section 2"], "metadata": {
                "table_structure": {"num_cols": 3, "num_rows": 5, "cells": [
                    {"row": 0, "col": 0, "text": "Phenomenon", "is_header": True},
                    {"row": 0, "col": 1, "text": "Frequency Range", "is_header": True},
                    {"row": 0, "col": 2, "text": "Level", "is_header": True},
                ]}
            }},
            {"node_type": "table", "heading_path": [], "metadata": {
                "table_structure": {"num_cols": 1, "num_rows": 3, "cells": [
                    {"row": 0, "col": 0, "text": "column_1", "is_header": True},
                ]}
            }},
        ]
        (doc_dir / "canonical_nodes.json").write_text(json.dumps(nodes))

        validator = ExtractionValidator(tmp_path)
        metrics = validator.validate_document("doc002")

        assert metrics.total_nodes == 3
        assert metrics.total_tables == 2
        assert metrics.total_clauses == 1
        assert metrics.tables_with_headers == 1  # one table has proper headers
        assert len(metrics.errors) == 0

    def test_validate_document_reads_filename_from_metadata(self, tmp_path):
        from grc_policy_server.services.ingestion.extraction_validator import ExtractionValidator

        doc_dir = tmp_path / "doc003"
        doc_dir.mkdir()
        (doc_dir / "canonical_nodes.json").write_text("[]")
        (doc_dir / "metadata.json").write_text(json.dumps({"filename": "TL_81000.pdf"}))

        validator = ExtractionValidator(tmp_path)
        metrics = validator.validate_document("doc003")
        assert metrics.filename == "TL_81000.pdf"

    def test_section_coverage_pct(self, tmp_path):
        from grc_policy_server.services.ingestion.extraction_validator import ExtractionValidator

        doc_dir = tmp_path / "doc004"
        doc_dir.mkdir()
        nodes = [
            {"node_type": "paragraph", "heading_path": ["Sec 1"], "metadata": {}},
            {"node_type": "paragraph", "heading_path": [], "metadata": {}},
        ]
        (doc_dir / "canonical_nodes.json").write_text(json.dumps(nodes))

        validator = ExtractionValidator(tmp_path)
        metrics = validator.validate_document("doc004")
        assert metrics.section_coverage_pct == 0.5

    def test_stitched_table_detection(self, tmp_path):
        from grc_policy_server.services.ingestion.extraction_validator import ExtractionValidator

        doc_dir = tmp_path / "doc005"
        doc_dir.mkdir()
        nodes = [
            {"node_type": "table", "heading_path": ["Sec"], "metadata": {
                "page_split_merged": True,
                "table_structure": {"num_cols": 2, "cells": []},
            }},
            {"node_type": "table", "heading_path": ["Sec"], "metadata": {
                "table_structure": {"num_cols": 2, "cells": []},
            }},
        ]
        (doc_dir / "canonical_nodes.json").write_text(json.dumps(nodes))

        validator = ExtractionValidator(tmp_path)
        metrics = validator.validate_document("doc005")
        assert metrics.tables_multi_page_stitched == 1

    def test_validate_all_uploads_skips_underscore_dirs(self, tmp_path):
        from grc_policy_server.services.ingestion.extraction_validator import ExtractionValidator

        # Regular doc
        d1 = tmp_path / "docA"
        d1.mkdir()
        (d1 / "canonical_nodes.json").write_text("[]")

        # Underscore-prefixed (cache/trace dirs) should be skipped
        d2 = tmp_path / "_comparison_cache"
        d2.mkdir()
        (d2 / "canonical_nodes.json").write_text("[]")

        validator = ExtractionValidator(tmp_path)
        results = validator.validate_all_uploads()
        doc_ids = [m.document_id for m in results]
        assert "docA" in doc_ids
        assert "_comparison_cache" not in doc_ids

    def test_compare_before_after(self, tmp_path):
        from grc_policy_server.services.ingestion.extraction_validator import ExtractionValidator

        validator = ExtractionValidator(tmp_path)
        before = [
            {"node_type": "table", "metadata": {}},
            {"node_type": "paragraph", "metadata": {}},
        ]
        after = [
            {"node_type": "table", "metadata": {"page_split_merged": True}},
            {"node_type": "paragraph", "metadata": {}},
            {"node_type": "table", "metadata": {}},
        ]
        delta = validator.compare_before_after(before, after)
        assert delta["delta_tables"] == 1
        assert delta["tables_before"] == 1
        assert delta["tables_after"] == 2
        assert delta["stitched_tables_after"] == 1

    def test_print_report_no_crash(self, tmp_path, capsys):
        from grc_policy_server.services.ingestion.extraction_validator import (
            ExtractionMetrics,
            ExtractionValidator,
        )

        validator = ExtractionValidator(tmp_path)
        metrics = [
            ExtractionMetrics(
                document_id="x",
                filename="test.pdf",
                total_nodes=10,
                total_tables=2,
                header_quality_pct=0.75,
                section_coverage_pct=0.9,
            )
        ]
        validator.print_report(metrics)
        captured = capsys.readouterr()
        assert "test.pdf" in captured.out

    def test_validate_uploads_against_real_data(self, tmp_path):
        """Smoke test: if data/uploads exists, validate runs without errors."""
        import os
        real_uploads = Path(os.getcwd()) / "data" / "uploads"
        if not real_uploads.exists():
            pytest.skip("data/uploads not present")

        from grc_policy_server.services.ingestion.extraction_validator import ExtractionValidator

        validator = ExtractionValidator(real_uploads)
        results = validator.validate_all_uploads()
        for m in results:
            # Should complete without exceptions stored as errors
            assert isinstance(m.total_nodes, int)
            assert 0.0 <= m.header_quality_pct <= 1.0
            assert 0.0 <= m.section_coverage_pct <= 1.0
