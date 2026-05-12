"""Tests for Phase A CIR model extensions.

Covers NormalizedFact, Citation, TableCell new fields,
CanonicalTable CIR fields, and CanonicalNode provenance fields.
"""

from __future__ import annotations

import pytest

from grc_policy_server.services.documents.canonical_table_model import (
    BBox,
    CanonicalTable,
    Citation,
    NormalizedFact,
    TableCell,
    TableColumn,
    TableRow,
)
from grc_policy_server.services.documents.canonical_models import CanonicalNode
from grc_policy_server.services.documents.cir_models import Citation as CirCitation, NormalizedFact as CirFact


class TestNormalizedFact:
    def test_construction(self):
        fact = NormalizedFact(
            fact_id="NF-001",
            owner_object_id="tbl_001",
            fact_type="frequency_range",
            name="frequency_range",
            value="150000.0-30000000.0",
            unit="Hz",
            raw_value="150 kHz – 30 MHz",
            confidence=0.95,
        )
        assert fact.fact_id == "NF-001"
        assert fact.fact_type == "frequency_range"
        assert fact.unit == "Hz"
        assert fact.confidence == 0.95

    def test_defaults(self):
        fact = NormalizedFact(
            fact_id="NF-002",
            owner_object_id="node_1",
            fact_type="normative_term",
            name="normative_term",
            value="shall",
        )
        assert fact.unit == ""
        assert fact.raw_value == ""
        assert fact.confidence == 1.0

    def test_to_dict(self):
        fact = NormalizedFact(
            fact_id="NF-003",
            owner_object_id="tbl_x",
            fact_type="field_strength",
            name="field_strength",
            value="30",
            unit="V/m",
            raw_value="30 V/m",
        )
        d = fact.to_dict()
        assert d["fact_id"] == "NF-003"
        assert d["unit"] == "V/m"
        assert d["fact_type"] == "field_strength"

    def test_immutable(self):
        fact = NormalizedFact(
            fact_id="NF-004",
            owner_object_id="x",
            fact_type="numeric",
            name="n",
            value="1",
        )
        with pytest.raises(Exception):
            fact.fact_id = "changed"  # type: ignore[misc]


class TestCitation:
    def test_construction_without_bbox(self):
        cit = Citation(citation_id="CIT-001", source_node_id="node_42", page=3)
        assert cit.citation_id == "CIT-001"
        assert cit.bbox is None
        assert cit.text_snippet == ""

    def test_construction_with_bbox(self):
        bbox = BBox(x0=10, y0=20, x1=200, y1=40, page=3)
        cit = Citation(
            citation_id="CIT-002",
            source_node_id="node_5",
            page=3,
            bbox=bbox,
            text_snippet="shall withstand",
        )
        assert cit.bbox is not None
        assert cit.text_snippet == "shall withstand"

    def test_to_dict(self):
        cit = Citation(citation_id="CIT-003", source_node_id="n", page=1)
        d = cit.to_dict()
        assert d["citation_id"] == "CIT-003"
        assert d["bbox"] is None

    def test_to_dict_with_bbox(self):
        bbox = BBox(x0=0, y0=0, x1=100, y1=20, page=2)
        cit = Citation(citation_id="C", source_node_id="n", page=2, bbox=bbox)
        d = cit.to_dict()
        assert d["bbox"]["page"] == 2


class TestCirModelsReexport:
    def test_reexported_types_are_same(self):
        assert CirFact is NormalizedFact
        assert CirCitation is Citation


class TestTableCellCirFields:
    def test_new_fields_have_defaults(self):
        cell = TableCell(row=0, col=0)
        assert cell.semantic_key == ""
        assert cell.normalized_facts == []
        assert cell.citations == []
        assert cell.footnote_refs == []

    def test_can_assign_normalized_facts(self):
        fact = NormalizedFact(
            fact_id="NF-x",
            owner_object_id="t",
            fact_type="field_strength",
            name="fs",
            value="30",
            unit="V/m",
        )
        cell = TableCell(row=0, col=0, normalized_facts=[fact])
        assert len(cell.normalized_facts) == 1
        assert cell.normalized_facts[0].fact_type == "field_strength"

    def test_to_dict_includes_cir_fields(self):
        cell = TableCell(
            row=1,
            col=2,
            text="shall",
            semantic_key="NormativeTerm",
            footnote_refs=["fn1"],
        )
        d = cell.to_dict()
        assert d["semantic_key"] == "NormativeTerm"
        assert d["footnote_refs"] == ["fn1"]
        assert d["normalized_facts"] == []
        assert d["citations"] == []


class TestCanonicalTableCirFields:
    def _make_table(self, **kwargs) -> CanonicalTable:
        return CanonicalTable(
            table_uid="t001",
            caption_original="Test Table",
            caption_normalized="test table",
            section_path=["6", "6.1"],
            pages=[1],
            columns=[TableColumn(index=0, name="Col", normalized="col")],
            rows=[TableRow(row_number=0, cells=[TableCell(row=0, col=0, text="x")])],
            **kwargs,
        )

    def test_new_fields_have_defaults(self):
        t = self._make_table()
        assert t.footnote_refs == []
        assert t.multi_page_stitched is False
        assert t.stitched_from_pages == []
        assert t.stitching_score == 0.0

    def test_stitching_fields(self):
        t = self._make_table(
            multi_page_stitched=True,
            stitched_from_pages=[1, 2],
            stitching_score=0.85,
        )
        assert t.multi_page_stitched is True
        assert t.stitching_score == 0.85

    def test_to_dict_has_cir_key(self):
        t = self._make_table(footnote_refs=["a"], multi_page_stitched=True, stitching_score=0.72)
        d = t.to_dict()
        assert "cir" in d
        assert d["cir"]["multi_page_stitched"] is True
        assert d["cir"]["footnote_refs"] == ["a"]
        assert d["cir"]["stitching_score"] == 0.72


class TestCanonicalNodeProvenance:
    def _make_node(self, **overrides) -> CanonicalNode:
        defaults = dict(
            node_id="n1",
            document_id="doc1",
            version_id="1.0",
            parent_id=None,
            node_type="paragraph",
            section_label=None,
            heading_path=[],
            order_index=0,
            raw_text="text",
            normalized_text="text",
            page_from=1,
            page_to=1,
            bbox_refs=[],
        )
        defaults.update(overrides)
        return CanonicalNode(**defaults)

    def test_default_provenance_fields(self):
        node = self._make_node()
        assert node.ocr_used is False
        assert node.text_density == 0.0
        assert node.has_native_text is True
        assert node.source_extractor == ""
        assert node.reading_order == -1

    def test_explicit_provenance_fields(self):
        node = self._make_node(
            ocr_used=True,
            text_density=1.5,
            has_native_text=False,
            source_extractor="docling",
            reading_order=3,
        )
        assert node.ocr_used is True
        assert node.source_extractor == "docling"
        assert node.reading_order == 3

    def test_to_dict_round_trip(self):
        node = self._make_node(source_extractor="opendataloader", reading_order=7)
        d = node.to_dict()
        restored = CanonicalNode.from_dict(d)
        assert restored.source_extractor == "opendataloader"
        assert restored.reading_order == 7

    def test_from_hierarchy_record_falls_through_to_defaults(self):
        record = {
            "node_id": "n2",
            "document_id": "doc1",
            "text": "hello",
            "metadata": {},
        }
        node = CanonicalNode.from_hierarchy_record(record)
        assert node.ocr_used is False
        assert node.source_extractor == ""

    def test_from_hierarchy_record_reads_metadata_keys(self):
        record = {
            "node_id": "n3",
            "document_id": "doc1",
            "text": "world",
            "metadata": {
                "ocr_used": True,
                "source_extractor": "docling",
                "reading_order": 5,
                "has_native_text": False,
                "text_density": 2.1,
            },
        }
        node = CanonicalNode.from_hierarchy_record(record)
        assert node.ocr_used is True
        assert node.source_extractor == "docling"
        assert node.reading_order == 5
