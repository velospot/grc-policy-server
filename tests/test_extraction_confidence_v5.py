"""Tests for extraction-accuracy improvements and the confidence-aware v5 stream.

Covers: OCR error normalization, math canonicalization equality, formula
confidence, merged-cell expansion, footnote markers, confidence propagation
into comparison records, evidence-confidence review gating, and the
compare_stream_v5 event contract (extraction_quality events, per-diff
confidence, confidence-gated LLM usage, human-review enqueueing).
"""
from __future__ import annotations

import pytest

from grc_policy_server.models.schemas import (
    DocumentReference,
    Document,
    KeyDifference,
)
from grc_policy_server.services.comparison.change_records import (
    evidence_extraction_confidence,
)
from grc_policy_server.services.documents.canonical_models import CanonicalNode
from grc_policy_server.services.ingestion.ocr_error_normalizer import (
    normalize_ocr_errors,
)
from grc_policy_server.services.ingestion.table_normalization import (
    expand_merged_cells,
    extract_footnote_markers,
    rows_from_cells,
)
from grc_policy_server.utils.hashing import normalize_for_comparison
from grc_policy_server.utils.math_text import (
    normalize_math_text,
    score_formula_confidence,
)


# ---------------------------------------------------------------------------
# OCR error normalization (A3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3O dB", "30 dB"),
        ("l50 kHz", "150 kHz"),
        ("1O0 V", "100 V"),
        ("1S0 mm", "150 mm"),
        ("rnuss", "muss"),
        ("lnitial", "Initial"),
        ("30 dB (µV/m)", "30 dBµV/m"),
    ],
)
def test_normalize_ocr_errors(raw: str, expected: str) -> None:
    assert normalize_ocr_errors(raw) == expected


def test_normalize_ocr_errors_leaves_prose_alone() -> None:
    text = "The Oscillator level is normal. Olso GmbH."
    assert normalize_ocr_errors(text) == text


# ---------------------------------------------------------------------------
# Math canonicalization (A4)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("30 dBμV/m", "30dBuV/m"),
        ("30 dBµV/m", "30 dB (µV/m)"),
        ("3 × 10⁶ Hz", "3x10^6 Hz"),
        ("13, 5 ± 0, 5 kV", "13.5±0.5 kV"),
        ("150 kHz", "150kHz"),
        ("30 – 230 MHz", "30-230 MHz"),
    ],
)
def test_math_variants_normalize_equal(left: str, right: str) -> None:
    assert normalize_for_comparison(left) == normalize_for_comparison(right)


def test_superscript_exponent_not_flattened() -> None:
    # NFKC alone would turn 10⁶ into 106 — the exponent must survive as 10^6.
    assert "10^6" in normalize_math_text("3 × 10⁶")
    assert normalize_for_comparison("10⁶ Hz") != normalize_for_comparison("106 Hz")


def test_math_normalization_idempotent() -> None:
    text = "3 × 10⁶ Hz und 13, 5 ± 0, 5 kV bei 30 dB (µV/m)"
    once = normalize_math_text(text)
    assert normalize_math_text(once) == once


def test_decimal_comma_leaves_thousands_separator() -> None:
    assert normalize_math_text("1,000 samples") == "1,000 samples"
    # Decimal comma converted; value/unit spacing collapsed by design.
    assert normalize_math_text("13,5 kV") == "13.5kV"


# ---------------------------------------------------------------------------
# Formula confidence (A4)
# ---------------------------------------------------------------------------

def test_formula_confidence_scores() -> None:
    assert score_formula_confidence("") == 0.0
    assert score_formula_confidence(r"E = 20 \log_{10}(U/U_0)") >= 0.85
    assert score_formula_confidence(r"E = \frac{1}{2 m v^2") < 0.70  # unbalanced


# ---------------------------------------------------------------------------
# Merged cells + footnotes (B2)
# ---------------------------------------------------------------------------

def _span_cells() -> list[dict]:
    return [
        {"row": 0, "col": 0, "text": "Freq", "row_span": 1, "col_span": 1},
        {"row": 0, "col": 1, "text": "Limit", "row_span": 1, "col_span": 1},
        {"row": 1, "col": 0, "text": "30 MHz", "row_span": 2, "col_span": 1},
        {"row": 1, "col": 1, "text": "40 1)", "row_span": 1, "col_span": 1},
        {"row": 2, "col": 1, "text": "47", "row_span": 1, "col_span": 1},
    ]


def test_merged_cell_value_propagates_to_covered_rows() -> None:
    rows = rows_from_cells(_span_cells(), ["freq", "limit"], header_depth=1)
    assert rows[0]["row_data"]["freq"] == "30 MHz"
    assert rows[1]["row_data"]["freq"] == "30 MHz"


def test_expand_merged_cells_marks_propagated() -> None:
    expanded = expand_merged_cells(_span_cells())
    propagated = [c for c in expanded if c.get("propagated")]
    assert len(propagated) == 1
    assert propagated[0]["row"] == 2 and propagated[0]["col"] == 0


def test_extract_footnote_markers() -> None:
    assert extract_footnote_markers(_span_cells()) == ["1)"]
    assert extract_footnote_markers([{"text": "only a) marker a)"}]) == ["a)"]
    assert extract_footnote_markers([{"text": "plain value"}]) == []


# ---------------------------------------------------------------------------
# Confidence propagation into comparison records (C1)
# ---------------------------------------------------------------------------

def _node(**overrides) -> CanonicalNode:
    base = dict(
        node_id="n1",
        document_id="d1",
        version_id="1.0",
        parent_id=None,
        node_type="clause",
        section_label="5.2",
        heading_path=["5.2 Limits"],
        order_index=0,
        raw_text="text",
        normalized_text="text",
        page_from=3,
        page_to=3,
        bbox_refs=[],
    )
    base.update(overrides)
    return CanonicalNode(**base)


def test_comparison_record_default_confidence_is_trusted() -> None:
    record = _node().to_comparison_record()
    assert record["extraction_confidence"] == 1.0
    assert record["ocr_used"] is False
    assert record["confidence_flags"] == []


def test_comparison_record_table_uses_quality_score() -> None:
    record = _node(
        node_type="table",
        metadata={"extraction_quality_score": 0.61, "requires_review": True},
    ).to_comparison_record()
    assert record["extraction_confidence"] == 0.61
    assert record["requires_extraction_review"] is True


def test_comparison_record_ocr_node_uses_ocr_confidence() -> None:
    record = _node(
        ocr_used=True,
        metadata={"ocr_confidence": 0.42},
    ).to_comparison_record()
    assert record["extraction_confidence"] == 0.42
    assert record["ocr_used"] is True
    assert record["ocr_confidence"] == 0.42


def test_comparison_record_explicit_confidence_wins() -> None:
    record = _node(
        node_type="formula",
        metadata={"extraction_confidence": 0.3, "confidence_flags": ["unparsed_formula"]},
    ).to_comparison_record()
    assert record["extraction_confidence"] == 0.3
    assert record["confidence_flags"] == ["unparsed_formula"]


# ---------------------------------------------------------------------------
# Evidence confidence gating (C2)
# ---------------------------------------------------------------------------

def test_evidence_confidence_min_and_reasons() -> None:
    confidence, reasons = evidence_extraction_confidence(
        [
            {
                "extraction_confidence": 0.4,
                "ocr_used": True,
                "node_type": "table",
                "low_confidence_table": True,
            },
            {"extraction_confidence": 0.95},
        ]
    )
    assert confidence == 0.4
    assert "low_ocr_confidence" in reasons
    assert "low_confidence_table" in reasons


def test_evidence_confidence_trusts_legacy_nodes() -> None:
    assert evidence_extraction_confidence([{"text": "no confidence data"}, None]) == (
        1.0,
        [],
    )


def test_evidence_confidence_above_threshold_no_reasons() -> None:
    confidence, reasons = evidence_extraction_confidence(
        [{"extraction_confidence": 0.8}], review_threshold=0.7
    )
    assert confidence == 0.8
    assert reasons == []


# ---------------------------------------------------------------------------
# compare_stream_v5 event contract (C3 + D1)
# ---------------------------------------------------------------------------

class _FakeLLM:
    def __init__(self) -> None:
        self.diff_row_calls = 0

    async def generate_diff_table_row_stream(self, **kwargs):
        self.diff_row_calls += 1
        yield "limit tightened"

    async def summarize_changes(self, **kwargs) -> str:
        return "summary text"


class _FakeCanonicalStore:
    def load_comparison_nodes(self, document_id: str) -> list[dict]:
        return [
            {"extraction_confidence": 0.95, "ocr_used": False},
            {
                "extraction_confidence": 0.4,
                "ocr_used": True,
                "low_confidence_table": True,
                "requires_extraction_review": True,
            },
        ]

    def load_debug_artifacts(self, document_id: str) -> dict:
        return {}


class _FakeReviewQueue:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def enqueue(self, **kwargs) -> None:
        self.items.append(kwargs)


def _diff(
    *,
    impact: str,
    extraction_confidence: float | None = None,
    review_reasons: list[str] | None = None,
    requires_review: bool = False,
    node_id: str,
) -> KeyDifference:
    ref = DocumentReference(
        section="5.2 Limits", page=3, sourceText="30 dBuV/m", nodeId=node_id
    )
    return KeyDifference(
        changeType="MODIFIED",
        section="5.2 Limits",
        doc1Content="30 dBuV/m",
        doc2Content="27 dBuV/m",
        impact=impact,
        changeSeverity="high" if impact == "High" else "low",
        doc1Reference=ref,
        doc2Reference=ref,
        nodeType="table",
        requiresHumanReview=requires_review,
        extractionConfidence=extraction_confidence,
        reviewReasons=review_reasons or [],
    )


def test_compare_stream_v5_contract(monkeypatch) -> None:
    import asyncio

    from grc_policy_server.services.comparison import real_diff_engine_stream as mod

    diffs = [
        _diff(impact="High", extraction_confidence=0.95, node_id="high-1"),
        _diff(impact="Low", extraction_confidence=0.9, node_id="low-1"),
        _diff(
            impact="High",
            extraction_confidence=0.4,
            review_reasons=["low_confidence_table"],
            requires_review=True,
            node_id="lowconf-1",
        ),
    ]

    async def _fake_compare_records_only(self, doc1, doc2, **kwargs):
        return diffs, doc1.name, doc2.name, "en", [], None

    monkeypatch.setattr(
        mod.RealDiffEngine, "compare_records_only", _fake_compare_records_only
    )

    llm = _FakeLLM()
    review_queue = _FakeReviewQueue()
    engine = mod.RealDiffEngineStream(
        qdrant=None,
        neo4j=None,
        llm=llm,
        canonical_store=_FakeCanonicalStore(),
        review_queue=review_queue,
    )
    doc = lambda i: Document(  # noqa: E731
        id=i, name=i, version="1", uploadDate="-", size="-", category="EMC"
    )

    async def _collect() -> list[dict]:
        return [e async for e in engine.compare_stream_v5(doc("a"), doc("b"))]

    events = asyncio.run(_collect())
    by_type: dict[str, list[dict]] = {}
    for event in events:
        by_type.setdefault(event["type"], []).append(event)

    # extraction_quality event per document with node aggregates
    assert len(by_type["extraction_quality"]) == 2
    quality = by_type["extraction_quality"][0]
    assert quality["node_count"] == 2
    assert quality["ocr_nodes"] == 1
    assert quality["low_confidence_nodes"] == 1

    # Only the trusted High-impact diff used the LLM (D1 gating)
    assert llm.diff_row_calls == 1
    completes = {e["change_id"]: e for e in by_type["diff_complete"]}
    assert completes["high-1"]["analysis_source"] == "llm"
    assert completes["low-1"]["analysis_source"] == "deterministic"
    assert completes["lowconf-1"]["analysis_source"] == "deterministic"
    assert completes["lowconf-1"]["review_reasons"] == ["low_confidence_table"]
    assert completes["lowconf-1"]["extraction_confidence"] == 0.4

    # diff_start carries confidence too
    starts = {e["change_id"]: e for e in by_type["diff_start"]}
    assert starts["lowconf-1"]["extraction_confidence"] == 0.4

    # Low-confidence diff was enqueued for human review
    assert len(review_queue.items) == 1
    assert review_queue.items[0]["node_id"] == "lowconf-1"
    assert review_queue.items[0]["classification"]["review_reasons"] == [
        "low_confidence_table"
    ]

    # done event splits review counts and reports llm usage
    done = by_type["done"][0]
    assert done["requires_human_review"] is True
    assert done["extraction_review_count"] == 1
    assert done["semantic_review_count"] == 0
    assert done["llm_calls"] == 1
    assert done["extraction_quality"] is not None


# ---------------------------------------------------------------------------
# Docling native ConfidenceReport integration
# ---------------------------------------------------------------------------

def _docling_report(**page_overrides):
    from docling.datamodel.base_models import ConfidenceReport, PageConfidenceScores

    pages = {
        1: PageConfidenceScores(
            parse_score=0.95, layout_score=0.9, ocr_score=0.92
        ),
        2: PageConfidenceScores(parse_score=0.3, layout_score=0.4, ocr_score=0.35),
    }
    for page_no, overrides in page_overrides.items():
        pages[page_no] = PageConfidenceScores(**overrides)
    return ConfidenceReport(pages=pages)


def test_confidence_report_to_dict_nan_safe_and_json() -> None:
    import json

    from grc_policy_server.services.ingestion.docling_adapter import (
        confidence_report_to_dict,
    )

    assert confidence_report_to_dict(None) is None
    result = confidence_report_to_dict(_docling_report())
    # table_score was never set → NaN → None
    assert result["pages"][1]["table_score"] is None
    assert result["pages"][1]["parse_score"] == 0.95
    assert result["pages"][2]["low_grade"] == "poor"
    assert result["pages"][1]["mean_grade"] in {"good", "excellent"}
    assert result["mean_grade"] in {"poor", "fair", "good", "excellent"}
    json.dumps(result)  # must be JSON-serializable (no NaN)


def _chunk(page: int, chunk_type: str = "clause", metadata: dict | None = None):
    from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk

    return ParsedChunk(
        chunk_type=chunk_type,
        text="30 dBuV/m limit",
        section_path=("5.2 Limits",),
        page_number=page,
        ordinal=page,
        metadata=dict(metadata or {}),
    )


def test_annotate_chunks_flags_poor_pages_only() -> None:
    from grc_policy_server.services.ingestion.docling_adapter import (
        confidence_report_to_dict,
    )
    from grc_policy_server.services.ingestion.document_ingestion_service import (
        _annotate_chunks_with_page_confidence,
    )

    confidence = confidence_report_to_dict(_docling_report())
    healthy, poor = _annotate_chunks_with_page_confidence(
        [_chunk(1), _chunk(2)], confidence
    )
    # Healthy page: page scores attached, but no review-triggering confidence
    assert healthy.metadata["docling_page_confidence"]["parse_score"] == 0.95
    assert "extraction_confidence" not in healthy.metadata
    # Poor page: min(ocr, parse) becomes the node confidence + flags
    assert poor.metadata["extraction_confidence"] == 0.3
    assert "docling_poor_parse" in poor.metadata["confidence_flags"]
    assert "docling_poor_ocr" in poor.metadata["confidence_flags"]


def test_table_blend_uses_docling_page_score() -> None:
    import asyncio
    from pathlib import Path

    from grc_policy_server.services.ingestion.docling_adapter import (
        DoclingAdapter,
        confidence_report_to_dict,
    )
    from grc_policy_server.services.ingestion.document_ingestion_service import (
        DocumentIngestionService,
        _IngestionContext,
    )
    from grc_policy_server.services.llm.noop_llm import NoOpLLM

    service = DocumentIngestionService(
        docling_adapter=DoclingAdapter(),
        qdrant=None,
        neo4j=None,
        llm=NoOpLLM(),
        upload_root=Path("/tmp"),
    )
    confidence = confidence_report_to_dict(_docling_report())
    table_meta = {
        "table_headers": ["frequency", "limit"],
        "table_structure": {
            "num_rows": 2,
            "num_cols": 2,
            "cells": [
                {"row": 0, "col": 0, "text": "Frequency", "is_header": True},
                {"row": 0, "col": 1, "text": "Limit", "is_header": True},
                {"row": 1, "col": 0, "text": "30 MHz"},
                {"row": 1, "col": 1, "text": "40 dBuV/m"},
            ],
        },
        "docling_page_confidence": confidence["pages"][2],
    }
    context = _IngestionContext(
        filename="t.pdf",
        content=b"x",
        content_type="application/pdf",
        document_id="doc-1",
        content_hash="h",
        parsed_chunks=[_chunk(2, chunk_type="table", metadata=table_meta)],
        docling_confidence=confidence,
    )
    context = asyncio.run(service._stage_normalize_tables(context))
    table = context.parsed_chunks[0]
    # POOR page (low_grade) must be flagged and drag the blend down
    assert "docling_low_confidence_page" in table.metadata["confidence_flags"]
    local_only = 0.5 * table.metadata["extraction_quality_score"] + 0.5 * 1.0
    assert table.metadata["extraction_confidence"] < local_only
    # Document metrics carry the docling summary (without per-page detail)
    metrics = context.confidence_metrics
    assert metrics["docling_confidence"]["low_grade"] in {
        "poor", "fair", "good", "excellent",
    }
    assert "pages" not in metrics["docling_confidence"]


def test_document_metrics_flag_poor_low_grade() -> None:
    from grc_policy_server.services.ingestion.document_ingestion_service import (
        _build_document_confidence_metrics,
    )

    metrics = _build_document_confidence_metrics(
        "doc-1",
        [],
        docling_confidence={"low_grade": "poor", "low_score": 0.2, "mean_grade": "fair"},
    )
    assert metrics["requires_human_review_count"] == 1
    assert any(
        flag["flag_type"] == "docling_low_grade"
        for flag in metrics["extraction_flags"]
    )


def test_extraction_quality_summary_exposes_docling_grades() -> None:
    from grc_policy_server.services.comparison.real_diff_engine_stream import (
        RealDiffEngineStream,
    )

    class _StoreWithDocling(_FakeCanonicalStore):
        def load_debug_artifacts(self, document_id: str) -> dict:
            return {
                "hierarchyJson": {
                    "metadata": {
                        "docling_confidence": {
                            "mean_grade": "good",
                            "low_grade": "fair",
                            "parse_score": 0.91,
                            "layout_score": 0.88,
                            "table_score": None,
                            "ocr_score": None,
                            "mean_score": 0.9,
                            "low_score": 0.7,
                        }
                    }
                }
            }

    engine = RealDiffEngineStream(
        qdrant=None, neo4j=None, llm=_FakeLLM(), canonical_store=_StoreWithDocling()
    )
    summary = engine._extraction_quality_summary("doc-1")
    assert summary["docling_mean_grade"] == "good"
    assert summary["docling_low_grade"] == "fair"
    assert summary["docling_scores"]["parse_score"] == 0.91
