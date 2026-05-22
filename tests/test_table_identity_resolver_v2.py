"""Tests for Phase D multi-page table stitching with 6-factor continuation score."""

from __future__ import annotations

import pytest

from grc_policy_server.services.ingestion.table_extraction_ensemble import TableCandidate
from grc_policy_server.services.ingestion.table_identity_resolver import TableIdentityResolver


def _cand(
    page: int,
    headers: list[str],
    caption: str = "",
    x0: float = 50.0,
    x1: float = 550.0,
    num_rows: int = 5,
    cells: list | None = None,
    confidence: float = 0.8,
    backend: str = "pdfplumber",
) -> TableCandidate:
    return TableCandidate(
        backend_name=backend,
        page_number=page,
        bbox={"x0": x0, "y0": 100.0, "x1": x1, "y1": 400.0},
        cells=cells or [],
        headers=headers,
        num_rows=num_rows,
        num_cols=len(headers),
        confidence=confidence,
        metadata={"caption_original": caption},
    )


class TestContinuationScore:
    resolver = TableIdentityResolver()

    def test_consecutive_pages_same_headers_scores_high(self):
        c1 = _cand(1, ["Phenomenon", "Frequency Range", "Level"], caption="Table 5")
        c2 = _cand(2, ["Phenomenon", "Frequency Range", "Level"], caption="Table 5")
        score = self.resolver._continuation_score(c1, c2, [], [])
        assert score >= 0.70

    def test_different_column_count_scores_zero(self):
        c1 = _cand(1, ["A", "B", "C"])
        c2 = _cand(2, ["A", "B"])
        score = self.resolver._continuation_score(c1, c2, [], [])
        assert score == 0.0

    def test_far_apart_pages_lower_adjacency(self):
        c1 = _cand(1, ["A", "B"])
        c2 = _cand(5, ["A", "B"])
        score = self.resolver._continuation_score(c1, c2, [], [])
        # adjacency score = 0 for page diff > 2, so max possible ≤ 0.80
        assert score < 0.70

    def test_x_misaligned_tables_lower_score(self):
        c1 = _cand(1, ["A", "B"], x0=50.0)
        c2 = _cand(2, ["A", "B"], x0=200.0)  # 150pt offset
        score = self.resolver._continuation_score(c1, c2, [], [])
        # Alignment factor hurt; should be lower than well-aligned pair
        aligned_score = self.resolver._continuation_score(
            _cand(1, ["A", "B"], x0=50.0),
            _cand(2, ["A", "B"], x0=55.0),
            [], []
        )
        assert score < aligned_score


class TestRepeatedHeaderScore:
    resolver = TableIdentityResolver()

    def test_repeated_headers_detect(self):
        headers = ["Phenomenon", "Frequency", "Level"]
        c1 = _cand(1, headers)
        # c2 has its first row repeating c1 headers
        cells_c2 = [
            {"row": 0, "col": 0, "text": "Phenomenon"},
            {"row": 0, "col": 1, "text": "Frequency"},
            {"row": 0, "col": 2, "text": "Level"},
            {"row": 1, "col": 0, "text": "BCI"},
        ]
        c2 = _cand(2, headers, cells=cells_c2)
        score = self.resolver._repeated_header_score(c1, c2)
        assert score >= 0.8

    def test_no_repeat_scores_zero(self):
        c1 = _cand(1, ["A", "B"])
        c2 = _cand(2, ["A", "B"], cells=[{"row": 0, "col": 0, "text": "data"}])
        score = self.resolver._repeated_header_score(c1, c2)
        assert score == 0.0


class TestAdjacencyScore:
    resolver = TableIdentityResolver()

    def test_consecutive_pages_score_one(self):
        c1 = _cand(3, ["A"])
        c2 = _cand(4, ["A"])
        assert self.resolver._adjacency_score(c1, c2) == 1.0

    def test_one_page_gap_score_half(self):
        c1 = _cand(3, ["A"])
        c2 = _cand(5, ["A"])
        assert self.resolver._adjacency_score(c1, c2) == 0.5

    def test_large_gap_score_zero(self):
        c1 = _cand(1, ["A"])
        c2 = _cand(10, ["A"])
        assert self.resolver._adjacency_score(c1, c2) == 0.0


class TestAlignmentScore:
    resolver = TableIdentityResolver()

    def test_exact_alignment_score_one(self):
        c1 = _cand(1, ["A"], x0=50.0)
        c2 = _cand(2, ["A"], x0=50.0)
        assert self.resolver._alignment_score(c1, c2) == 1.0

    def test_50pt_offset_half_score(self):
        c1 = _cand(1, ["A"], x0=0.0)
        c2 = _cand(2, ["A"], x0=50.0)
        score = self.resolver._alignment_score(c1, c2)
        assert 0.4 <= score <= 0.6

    def test_100pt_offset_zero(self):
        c1 = _cand(1, ["A"], x0=0.0)
        c2 = _cand(2, ["A"], x0=100.0)
        assert self.resolver._alignment_score(c1, c2) == 0.0


class TestAreCandidatesSimilar:
    resolver = TableIdentityResolver()

    def test_continuation_pair_is_similar(self):
        c1 = _cand(1, ["Phenomenon", "Frequency Range", "Level"], caption="Table 5 Radiated Immunity")
        c2 = _cand(2, ["Phenomenon", "Frequency Range", "Level"], caption="Table 5 Radiated Immunity")
        assert self.resolver._are_candidates_similar(c1, c2) is True

    def test_different_columns_not_similar(self):
        c1 = _cand(1, ["A", "B", "C"])
        c2 = _cand(2, ["X", "Y"])
        assert self.resolver._are_candidates_similar(c1, c2) is False

    def test_far_pages_not_similar(self):
        c1 = _cand(1, ["A", "B"])
        c2 = _cand(10, ["A", "B"])
        assert self.resolver._are_candidates_similar(c1, c2) is False


class TestResolveTablesStitching:
    resolver = TableIdentityResolver()

    def test_split_table_identified(self):
        c1 = _cand(1, ["Phenomenon", "Level"], caption="Table 3")
        c2 = _cand(2, ["Phenomenon", "Level"], caption="Table 3")
        identities = self.resolver.resolve_tables([c1, c2])
        assert len(identities) == 1
        identity = list(identities.values())[0]
        assert identity.is_split is True
        assert 1 in identity.pages and 2 in identity.pages
        assert identity.stitching_score > 0.0

    def test_non_continuation_tables_stay_separate(self):
        c1 = _cand(1, ["A", "B", "C"], caption="Table 1")
        c2 = _cand(3, ["X", "Y", "Z"], caption="Table 2", x0=300.0)
        identities = self.resolver.resolve_tables([c1, c2])
        assert len(identities) == 2
