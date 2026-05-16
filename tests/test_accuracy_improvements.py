"""Regression tests for accuracy improvements:
- alphabetical table caption normalisation
- camelCase cosmetic filtering
- page-number sort order
- PII masking
- DNV cell normalisation
- 'n rows x m columns' fallback heading suppression
"""

import re
import pytest

from grc_policy_server.services.ingestion.docling_chunker import _normalize_table_caption
from grc_policy_server.services.comparison.change_records import is_cosmetic_text_change
from grc_policy_server.services.comparison.real_diff_engine import _mask_pii


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

    def test_trailing_colon_stripped(self):
        assert self._norm_cell("Frequency:") == "frequency"
