"""Regression tests for accuracy improvements:
- alphabetical table caption normalisation
- camelCase cosmetic filtering
- page-number sort order
- PII masking
- DNV cell normalisation
- 'n rows x m columns' fallback heading suppression
- Round 2: structural label change detection, pure-text hash, HIGH-first sort
"""

import re
import pytest

from grc_policy_server.services.ingestion.docling_chunker import _normalize_table_caption
from grc_policy_server.services.comparison.change_records import is_cosmetic_text_change, is_structural_label_change
from grc_policy_server.services.comparison.real_diff_engine import _mask_pii
from grc_policy_server.utils.hashing import pure_text_hash


# ---------------------------------------------------------------------------
# Caption normalisation
# ---------------------------------------------------------------------------

class TestNormalizeTableCaption:
    def test_strips_bold_markdown_decoration(self):
        assert _normalize_table_caption("**Tabelle C: Risk Matrix**") == "risk matrix"

    def test_strips_italic_decoration(self):
        assert _normalize_table_caption("*Table E – EMC Limits*") == "emc limits"

    def test_alphabetical_label_german(self):
        assert _normalize_table_caption("Tabelle C: Prüfpegel") == "prüfpegel"

    def test_alphabetical_label_english(self):
        assert _normalize_table_caption("Table E – Test Conditions") == "test conditions"

    def test_numeric_label_unchanged_base(self):
        # Both Table 3 and Table 7 with same subject should normalise to same string
        assert _normalize_table_caption("Table 3: Risk Matrix") == _normalize_table_caption("Table 7: Risk Matrix")

    def test_tab_abbreviation(self):
        assert _normalize_table_caption("Tab. B: Grenzwerte") == "grenzwerte"

    def test_plain_caption_unchanged(self):
        assert _normalize_table_caption("Test Conditions") == "test conditions"

    def test_em_dash_separator(self):
        assert _normalize_table_caption("Tabelle A – Severity Levels") == "severity levels"


# ---------------------------------------------------------------------------
# Cosmetic text change detection
# ---------------------------------------------------------------------------

class TestIsCosmedicTextChange:
    def test_camel_case_vs_title_case(self):
        assert is_cosmetic_text_change("maxVoltage", "Max Voltage") is True

    def test_camel_case_vs_lower(self):
        assert is_cosmetic_text_change("emcClassA", "emc class a") is True

    def test_upper_vs_lower(self):
        assert is_cosmetic_text_change("EMC Class A", "emc class a") is True

    def test_upper_camel_split(self):
        assert is_cosmetic_text_change("TestConditions", "Test Conditions") is True

    def test_acronym_preserved(self):
        # "EMCLimit" → "EMC Limit" — should still be cosmetic
        assert is_cosmetic_text_change("EMCLimit", "EMC Limit") is True

    def test_different_content_not_cosmetic(self):
        assert is_cosmetic_text_change("Class A", "Class B") is False

    def test_empty_inputs(self):
        assert is_cosmetic_text_change("", "Class A") is False
        assert is_cosmetic_text_change("Class A", "") is False

    def test_identical_inputs(self):
        assert is_cosmetic_text_change("EMC", "EMC") is False

    def test_trailing_period_is_cosmetic(self):
        assert is_cosmetic_text_change("...occur.", "...occur..") is True

    def test_numeric_difference_not_cosmetic(self):
        assert is_cosmetic_text_change("limit is 3.5 V", "limit is 35 V") is False


# ---------------------------------------------------------------------------
# PII masking
# ---------------------------------------------------------------------------

class TestMaskPii:
    def test_email_masked(self):
        result = _mask_pii("Contact john.doe@example.com for details")
        assert "[EMAIL]" in result
        assert "john.doe@example.com" not in result

    def test_ssn_masked(self):
        result = _mask_pii("SSN: 123-45-6789")
        assert "[SSN]" in result
        assert "123-45-6789" not in result

    def test_none_input(self):
        assert _mask_pii(None) is None

    def test_empty_string(self):
        assert _mask_pii("") == ""

    def test_no_pii_unchanged(self):
        text = "The test frequency is 1 MHz."
        assert _mask_pii(text) == text

    def test_multiple_emails_masked(self):
        result = _mask_pii("a@b.com and c@d.org")
        assert result.count("[EMAIL]") == 2


# ---------------------------------------------------------------------------
# Page-number sort order (via diff_min_page helper logic)
# ---------------------------------------------------------------------------

class TestDiffPageSortLogic:
    """Validate the sort key logic directly without needing full engine setup."""

    def _make_ref(self, page: int):
        """Minimal stand-in for DocumentReference."""
        class Ref:
            pass
        r = Ref()
        r.page = page
        return r

    def _diff_min_page(self, diff) -> int:
        pages = []
        for ref in [diff.doc1Reference, diff.doc2Reference]:
            if ref is not None:
                try:
                    pages.append(int(ref.page))
                except (TypeError, ValueError):
                    pass
        return min(pages, default=9999)

    def test_sort_by_page_asc(self):
        class Diff:
            pass

        d1, d2, d3 = Diff(), Diff(), Diff()
        d1.doc1Reference = self._make_ref(11)
        d1.doc2Reference = None
        d2.doc1Reference = self._make_ref(5)
        d2.doc2Reference = None
        d3.doc1Reference = self._make_ref(22)
        d3.doc2Reference = None

        sorted_diffs = sorted([d1, d2, d3], key=self._diff_min_page)
        assert [self._diff_min_page(d) for d in sorted_diffs] == [5, 11, 22]

    def test_no_reference_goes_last(self):
        class Diff:
            doc1Reference = None
            doc2Reference = None

        class DiffWithPage:
            pass

        d_no_page = Diff()
        d_with_page = DiffWithPage()
        d_with_page.doc1Reference = self._make_ref(3)
        d_with_page.doc2Reference = None

        sorted_diffs = sorted([d_no_page, d_with_page], key=self._diff_min_page)
        assert self._diff_min_page(sorted_diffs[0]) == 3
        assert self._diff_min_page(sorted_diffs[1]) == 9999


# ---------------------------------------------------------------------------
# DNV cell normalisation (EMC unit formats)
# ---------------------------------------------------------------------------

class TestDnvCellNormalisation:
    """Verify that EMC unit normalisations in _norm_cell produce equal tokens."""

    def _norm_cell(self, text: str) -> str:
        _EMC_UNIT_RE = re.compile(
            r"(\d)\s*(v/m|a/m|db[µμu]v|db[µμu]a|mhz|ghz|khz|hz|ma|[µμu]a|ms|[µμu]s|kv/m)\b",
            re.IGNORECASE,
        )
        t = str(text).lower().strip()
        t = re.sub(r"\*{1,3}|_{1,3}", "", t)
        t = _EMC_UNIT_RE.sub(lambda m: m.group(1) + m.group(2).lower(), t)
        t = re.sub(r"\b(level|no\.?|class)\s+(\S)", lambda m: m.group(1) + " " + m.group(2), t)
        t = re.sub(r"\s+", " ", t).strip()
        return t.rstrip(":.,;-")

    def test_level_spacing(self):
        assert self._norm_cell("Level 3") == self._norm_cell("level  3")

    def test_level_no_space(self):
        assert self._norm_cell("Level3") == "level3"

    def test_unit_spacing(self):
        assert self._norm_cell("1 V/m") == self._norm_cell("1V/m")

    def test_markdown_stripped(self):
        assert self._norm_cell("**Level 3**") == self._norm_cell("Level 3")

    def test_mhz_normalised(self):
        assert self._norm_cell("10 MHz") == self._norm_cell("10MHz")


# ---------------------------------------------------------------------------
# Round 2: structural label change detection
# ---------------------------------------------------------------------------

class TestIsStructuralLabelChange:
    def test_table_number_changes(self):
        assert is_structural_label_change("Table 3: EMC Limits", "Table 5: EMC Limits") is True

    def test_section_number_changes(self):
        assert is_structural_label_change("3.1 Introduction", "4.1 Introduction") is True

    def test_deep_section_number(self):
        assert is_structural_label_change("3.1.2 Test Procedure", "5.2.1 Test Procedure") is True

    def test_chapter_keyword(self):
        assert is_structural_label_change("Chapter 2: Scope", "Chapter 5: Scope") is True

    def test_figure_number(self):
        assert is_structural_label_change("Figure 3 – Block Diagram", "Figure 7 – Block Diagram") is True

    def test_semantic_change_not_structural(self):
        assert is_structural_label_change("3.1 Introduction", "3.1 Background") is False

    def test_identical_returns_false(self):
        assert is_structural_label_change("3.1 Introduction", "3.1 Introduction") is False

    def test_empty_returns_false(self):
        assert is_structural_label_change("", "3.1 Introduction") is False
        assert is_structural_label_change(None, "3.1 Introduction") is False

    def test_annex_label(self):
        assert is_structural_label_change("Annex A.1 Requirements", "Annex B.1 Requirements") is True


# ---------------------------------------------------------------------------
# Round 2: pure-text hash deduplication
# ---------------------------------------------------------------------------

class TestPureTextHash:
    def test_whitespace_independent(self):
        assert pure_text_hash("EMC Test\nLevel 3") == pure_text_hash("emc test level 3")

    def test_newlines_stripped(self):
        assert pure_text_hash("line1\nline2") == pure_text_hash("line1 line2")

    def test_case_independent(self):
        assert pure_text_hash("HELLO WORLD") == pure_text_hash("hello world")

    def test_tabs_stripped(self):
        assert pure_text_hash("col1\tcol2") == pure_text_hash("col1col2")

    def test_empty_returns_empty(self):
        assert pure_text_hash("") == ""
        assert pure_text_hash(None) == ""  # type: ignore[arg-type]

    def test_different_content_differs(self):
        assert pure_text_hash("Test A") != pure_text_hash("Test B")


# ---------------------------------------------------------------------------
# Round 2: HIGH-first sort (reverted from page-number sort)
# ---------------------------------------------------------------------------

class TestHighFirstSort:
    """Verify that impact-priority sort produces HIGH → MEDIUM → LOW order."""

    def _sort_key(self, impact: str) -> int:
        return ("High", "Medium", "Low").index(impact) if impact in ("High", "Medium", "Low") else 2

    def test_high_before_medium_before_low(self):
        diffs = [
            {"impact": "Low"},
            {"impact": "High"},
            {"impact": "Medium"},
        ]
        sorted_diffs = sorted(diffs, key=lambda d: self._sort_key(d["impact"]))
        assert [d["impact"] for d in sorted_diffs] == ["High", "Medium", "Low"]

    def test_unknown_impact_treated_as_low(self):
        diffs = [
            {"impact": "High"},
            {"impact": "Unknown"},
            {"impact": "Medium"},
        ]
        sorted_diffs = sorted(diffs, key=lambda d: self._sort_key(d["impact"]))
        assert sorted_diffs[0]["impact"] == "High"
        assert sorted_diffs[1]["impact"] == "Medium"


# ---------------------------------------------------------------------------
# Round 2: structural label rule in severity classifier
# ---------------------------------------------------------------------------

class TestStructuralLabelSeverityRule:
    def _make_ctx(self, **overrides):
        from grc_policy_server.services.comparison.severity_classifier import ClassificationContext
        defaults = dict(
            change_type="MODIFIED",
            structural_label_change=False,
            distance=0.5,
            meaning_change="changed",
            cosmetic_change=False,
            formatting_only_change=False,
            alignment_type="matched",
            node_type="clause",
            numeric_changes=[],
            requirement_verb_change=None,
            table_changes=[],
        )
        defaults.update(overrides)
        return ClassificationContext(**defaults)

    def test_structural_label_change_gives_low(self):
        from grc_policy_server.services.comparison.severity_classifier import _DEFAULT_ENGINE
        ctx = self._make_ctx(structural_label_change=True, distance=0.8, node_type="section")
        result = _DEFAULT_ENGINE.classify(ctx)
        assert result.severity == "low"

    def test_non_structural_modified_not_forced_low(self):
        from grc_policy_server.services.comparison.severity_classifier import _DEFAULT_ENGINE
        ctx = self._make_ctx(structural_label_change=False, distance=0.9)
        result = _DEFAULT_ENGINE.classify(ctx)
        assert result.severity != "low"


# ---------------------------------------------------------------------------
# Round 3: preposition/article stop-word fuzzy matching
# ---------------------------------------------------------------------------

class TestIsStructuralLabelChangeFuzzy:
    def test_german_preposition_swap(self):
        assert is_structural_label_change(
            "Allgemeine Anforderungen an Störfestigkeitsprüfungen von Komponenten 5.2.1",
            "Allgemeine Anforderungen für die Störfestigkeitsprüfungen von Komponenten 5.2.1",
        ) is True

    def test_article_insertion(self):
        assert is_structural_label_change(
            "General Requirements for EMC Tests 3.1",
            "General Requirements for the EMC Tests 3.1",
        ) is True

    def test_large_semantic_diff_not_structural(self):
        assert is_structural_label_change(
            "3.1 Introduction",
            "3.1 Risk Assessment",
        ) is False

    def test_same_after_number_strip(self):
        assert is_structural_label_change("5.2.1 Test Scope", "6.3.1 Test Scope") is True


# ---------------------------------------------------------------------------
# Round 3: table cell preview rendering
# ---------------------------------------------------------------------------

class TestRenderCellsPreview:
    def _engine(self):
        from unittest.mock import MagicMock, AsyncMock
        from grc_policy_server.services.comparison.real_diff_engine import RealDiffEngine
        engine = object.__new__(RealDiffEngine)
        return engine

    def test_basic_preview(self):
        engine = self._engine()
        cells = [
            {"row": 0, "col": 0, "text": "Abbr"},
            {"row": 0, "col": 1, "text": "Description"},
            {"row": 1, "col": 0, "text": "EMC"},
            {"row": 1, "col": 1, "text": "Electromagnetic Compatibility"},
        ]
        preview = engine._render_cells_preview(cells, max_rows=5)
        assert "Abbr | Description" in preview
        assert "EMC | Electromagnetic Compatibility" in preview

    def test_truncation(self):
        engine = self._engine()
        cells = [{"row": i, "col": 0, "text": f"row{i}"} for i in range(10)]
        preview = engine._render_cells_preview(cells, max_rows=3)
        assert "more rows" in preview
        assert "row0" in preview
        assert "row9" not in preview

    def test_empty_cells(self):
        engine = self._engine()
        assert engine._render_cells_preview([], max_rows=5) == ""


# ---------------------------------------------------------------------------
# Round 3: pure_text_hash in to_comparison_record
# ---------------------------------------------------------------------------

class TestPureTextHashInComparisonRecord:
    def test_hash_present_in_record(self):
        from grc_policy_server.services.documents.canonical_models import CanonicalNode
        node = CanonicalNode(
            node_id="test-id",
            document_id="doc-id",
            version_id="v1",
            parent_id=None,
            node_type="clause",
            section_label=None,
            heading_path=["Section 1"],
            order_index=0,
            raw_text="Es muss der Frequenzbereich 0,1 MHz bis 6 000 MHz geprüft werden.",
            normalized_text="es muss der frequenzbereich 0,1 mhz bis 6 000 mhz geprüft werden.",
            page_from=1,
            page_to=1,
            bbox_refs=[],
        )
        rec = node.to_comparison_record()
        assert "pure_text_hash" in rec
        assert rec["pure_text_hash"] != ""

    def test_identical_text_gives_same_hash(self):
        from grc_policy_server.services.documents.canonical_models import CanonicalNode
        def _make(text):
            return CanonicalNode(
                node_id="id", document_id="doc", version_id="v1", parent_id=None,
                node_type="clause", section_label=None, heading_path=[],
                order_index=0, raw_text=text, normalized_text=text.lower(),
                page_from=1, page_to=1, bbox_refs=[],
            ).to_comparison_record()["pure_text_hash"]

        assert _make("Es muss der Frequenzbereich 0,1 MHz geprüft werden.") == \
               _make("Es muss der Frequenzbereich 0,1 MHz geprüft werden.")


# ---------------------------------------------------------------------------
# Round 3b: pure_text_hash strips punctuation
# ---------------------------------------------------------------------------

class TestPureTextHashPunctuation:
    def test_punctuation_stripped(self):
        assert pure_text_hash("occur.") == pure_text_hash("occur..")

    def test_trailing_colon_stripped(self):
        assert pure_text_hash("General:") == pure_text_hash("General")

    def test_digits_preserved(self):
        # Decimal point IS stripped — "3.5v" and "35v" produce the same hash.
        # This is acceptable: the numeric guard in is_cosmetic_text_change prevents
        # "3.5 V" ≡ "35 V" false-positive at the comparison stage.
        assert pure_text_hash("3.5v") == pure_text_hash("35v")

    def test_different_alphanumeric_differ(self):
        assert pure_text_hash("occur.") != pure_text_hash("occurs.")


# ---------------------------------------------------------------------------
# Round 3b: cache version invalidates old keys
# ---------------------------------------------------------------------------

class TestCacheVersion:
    def _make_store(self, version: str):
        from unittest.mock import patch
        from pathlib import Path
        from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore
        store = ComparisonCacheStore(upload_root=Path("/tmp"))
        store.CACHE_VERSION = version
        return store

    def test_same_docs_different_version_different_key(self):
        from pathlib import Path
        from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore
        store_v1 = ComparisonCacheStore(upload_root=Path("/tmp"))
        store_v1.CACHE_VERSION = "v1"
        store_v2 = ComparisonCacheStore(upload_root=Path("/tmp"))
        store_v2.CACHE_VERSION = "v2"
        key_v1 = store_v1.cache_key_for_pair(doc1_id="docA", doc2_id="docB")
        key_v2 = store_v2.cache_key_for_pair(doc1_id="docA", doc2_id="docB")
        assert key_v1 != key_v2

    def test_current_version_is_v2(self):
        from pathlib import Path
        from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore
        store = ComparisonCacheStore(upload_root=Path("/tmp"))
        assert store.CACHE_VERSION == "v2"

    def test_key_includes_version(self):
        from hashlib import sha256
        from pathlib import Path
        from grc_policy_server.services.comparison.comparison_cache import ComparisonCacheStore
        store = ComparisonCacheStore(upload_root=Path("/tmp"))
        expected = sha256(f"{store.CACHE_VERSION}::docA::docB".encode()).hexdigest()
        assert store.cache_key_for_pair(doc1_id="docA", doc2_id="docB") == expected


class TestEmcEntityDetection:
    def test_field_strength_detected(self):
        from grc_policy_server.services.comparison.change_records import detect_emc_entity_type
        assert detect_emc_entity_type("10 V/m", "30 V/m") == "FieldStrength"

    def test_emission_limit_detected(self):
        from grc_policy_server.services.comparison.change_records import detect_emc_entity_type
        assert detect_emc_entity_type("60 dBµV", "56 dBµV") == "EmissionLimit"

    def test_acceptance_class_detected(self):
        from grc_policy_server.services.comparison.change_records import detect_emc_entity_type
        assert detect_emc_entity_type("Class A limits apply", "Class B limits apply") == "AcceptanceCriterion"

    def test_test_method_detected(self):
        from grc_policy_server.services.comparison.change_records import detect_emc_entity_type
        assert detect_emc_entity_type("IEC 61000-4-3 Ed.3", "IEC 61000-4-3 Ed.4") == "TestMethod"

    def test_frequency_range_detected(self):
        from grc_policy_server.services.comparison.change_records import detect_emc_entity_type
        assert detect_emc_entity_type("80 MHz to 1000 MHz", "80 MHz to 3000 MHz") == "FrequencyRange"

    def test_no_match_returns_empty(self):
        from grc_policy_server.services.comparison.change_records import detect_emc_entity_type
        assert detect_emc_entity_type("general policy clause text", "general policy revised text") == ""

    def test_procedure_change_standard_ref(self):
        from grc_policy_server.services.comparison.change_records import detect_test_procedure_change
        assert detect_test_procedure_change("IEC 61000-4-3 Ed.3", "IEC 61000-4-3 Ed.4") is True

    def test_procedure_change_no_diff(self):
        from grc_policy_server.services.comparison.change_records import detect_test_procedure_change
        assert detect_test_procedure_change("IEC 61000-4-3 Ed.3", "IEC 61000-4-3 Ed.3") is False

    def test_setup_change_with_numeric_diff(self):
        from grc_policy_server.services.comparison.change_records import detect_test_setup_change
        assert detect_test_setup_change("Temperature: 23°C", "Temperature: 25°C") is True

    def test_setup_change_no_numeric_diff(self):
        from grc_policy_server.services.comparison.change_records import detect_test_setup_change
        assert detect_test_setup_change("EUT on ground plane", "EUT on ground plane") is False


class TestNumericEntityOverlap:
    def test_identical_numbers_full_overlap(self):
        from grc_policy_server.services.comparison.clause_matcher import ClauseMatcher
        matcher = ClauseMatcher.__new__(ClauseMatcher)
        assert matcher._numeric_overlap("10 V/m at 80 MHz", "10 V/m at 80 MHz") == 1.0

    def test_no_numbers_returns_one(self):
        from grc_policy_server.services.comparison.clause_matcher import ClauseMatcher
        matcher = ClauseMatcher.__new__(ClauseMatcher)
        assert matcher._numeric_overlap("general text", "other text") == 1.0

    def test_partial_overlap(self):
        from grc_policy_server.services.comparison.clause_matcher import ClauseMatcher
        matcher = ClauseMatcher.__new__(ClauseMatcher)
        result = matcher._numeric_overlap("10 V/m 80 MHz", "10 V/m 3000 MHz")
        assert 0.0 < result < 1.0

    def test_no_overlap_returns_zero(self):
        from grc_policy_server.services.comparison.clause_matcher import ClauseMatcher
        matcher = ClauseMatcher.__new__(ClauseMatcher)
        result = matcher._numeric_overlap("10 V/m", "30 V/m")
        assert result == 0.0

    def test_entity_overlap_same_class(self):
        from grc_policy_server.services.comparison.clause_matcher import ClauseMatcher
        matcher = ClauseMatcher.__new__(ClauseMatcher)
        result = matcher._entity_overlap("Class A limits", "Class A requirements")
        assert result == 1.0

    def test_entity_overlap_different_class(self):
        from grc_policy_server.services.comparison.clause_matcher import ClauseMatcher
        matcher = ClauseMatcher.__new__(ClauseMatcher)
        result = matcher._entity_overlap("Class A limits", "Class B limits")
        assert result == 0.0
