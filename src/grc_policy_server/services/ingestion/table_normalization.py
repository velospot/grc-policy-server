from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from typing import Any

from grc_policy_server.utils.hashing import normalize_for_comparison


_HYPHEN_AT_END_RE = re.compile(r"-\s*$")


def _fuse_hyphenated_headers(headers: list[str]) -> list[str]:
    """Fuse adjacent header fragments produced by PDF word-break hyphenation.

    When the PDF extractor reads a cell whose text was visually broken by a
    soft hyphen across columns (e.g. "grenz-" | "wert u in db"), the result
    is two separate column headers instead of one.  This pass merges each
    hyphen-terminated header with its successor.
    """
    if len(headers) < 2:
        return headers
    result = list(headers)
    i = 0
    while i < len(result) - 1:
        if _HYPHEN_AT_END_RE.search(result[i]):
            fused = _HYPHEN_AT_END_RE.sub("", result[i]).strip() + result[i + 1]
            result[i] = fused.strip()
            result[i + 1] = ""
            i += 2
        else:
            i += 1
    return result


def normalize_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return normalize_for_comparison(text).strip()


def normalize_header(value: Any) -> str:
    text = normalize_cell(value).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_table_headers(headers: list[str]) -> list[str]:
    return [normalize_header(header) for header in headers]


def schema_signature(headers: list[str]) -> str:
    """SHA256 of normalized headers, skipping row-label stub columns.

    Stub columns (empty header in row 0, used as row-label margin) are tagged
    "_row_label_N" by extract_headers_from_cells().  Excluding them ensures that
    the same logical table produces the same signature regardless of whether the
    stub column is present or absent in a given PDF version.
    """
    stable = [
        h for h in normalize_table_headers(headers)
        if not h.startswith("_row_label_") and h != "column_1"
    ]
    canonical = " | ".join(stable)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# EMC/Safety/Environment column-role classification and unit normalization
# Based on AG-04-COMPARE.md specification
# ---------------------------------------------------------------------------

_LIMIT_COLUMNS: frozenset[str] = frozenset({
    "limit", "limit (dbμv/m)", "limit (dbuv/m)", "max level", "requirement",
    "limit level", "class limit", "grenzwert", "grenzwerte", "max", "maximum",
    "upper limit", "lower limit", "level limit", "emv grenzwert",
})
_MEASURED_COLUMNS: frozenset[str] = frozenset({
    "measured", "result", "level", "reading", "measured level", "test result",
    "messwert", "messung", "measured value", "peak", "average", "quasi-peak",
    "qp", "av", "pk", "ergebnis", "wert",
})
_MARGIN_COLUMNS: frozenset[str] = frozenset({
    "margin", "headroom", "margin (db)", "pass margin", "reserve", "abstand",
    "sicherheitsabstand", "safety margin", "db margin",
})
_RESULT_COLUMNS: frozenset[str] = frozenset({
    "pass/fail", "status", "verdict", "result", "pass", "fail",
    "bestanden", "ergebnis", "bewertung", "konformitat", "compliance",
    "p/f", "pass / fail",
})
_FREQUENCY_COLUMNS: frozenset[str] = frozenset({
    "frequency", "freq", "frequency (mhz)", "freq (hz)", "frequenz",
    "mhz", "ghz", "khz", "hz", "freq (khz)", "freq (ghz)", "frequency range",
})


def classify_columns(headers: list[str]) -> dict[int, str]:
    """Map column index → role string for EMC/Safety/Environment tables.

    Returns dict: {col_index: role} where role ∈
    {"limit", "measured", "margin", "result", "frequency", "other"}.
    Unrecognized columns get role "other".
    """
    roles: dict[int, str] = {}
    for idx, header in enumerate(headers):
        norm = normalize_header(header)
        # Try exact/partial matches in priority order
        if any(kw in norm for kw in _LIMIT_COLUMNS):
            roles[idx] = "limit"
        elif any(kw in norm for kw in _RESULT_COLUMNS):
            roles[idx] = "result"
        elif any(kw in norm for kw in _MARGIN_COLUMNS):
            roles[idx] = "margin"
        elif any(kw in norm for kw in _MEASURED_COLUMNS):
            roles[idx] = "measured"
        elif any(kw in norm for kw in _FREQUENCY_COLUMNS):
            roles[idx] = "frequency"
        else:
            roles[idx] = "other"
    return roles


# Unit normalization constants
_NUMERIC_UNIT_RE = re.compile(
    r"^\s*([<>≤≥]?\s*[+-]?\s*\d+(?:[.,]\d+)?(?:\s*[eE][+-]?\d+)?)\s*"
    r"(dbuv/m|dbµv/m|dbuv|dbµv|dbua/m|dbµa/m|dbm|v/m|mv/m|"
    r"mv|kv|v|a|ma|ka|ppm|mg/kg|%|db|dbc|dbd|dbw|w|kw|mw)\s*$",
    re.IGNORECASE,
)

_PASS_FAIL_VALUES: frozenset[str] = frozenset({
    "pass", "fail", "p", "f", "bestanden", "nicht bestanden",
    "ok", "nok", "yes", "no", "konform", "nicht konform",
    "compliant", "non-compliant", "✓", "✗", "x",
})
_PASS_VALUES: frozenset[str] = frozenset({
    "pass", "p", "bestanden", "ok", "yes", "konform", "compliant", "✓",
})
_FAIL_VALUES: frozenset[str] = frozenset({
    "fail", "f", "nicht bestanden", "nok", "no",
    "nicht konform", "non-compliant", "✗", "x",
})

_UNIT_TO_DB_UV_M: dict[str, float | None] = {
    "dbuv/m": 1.0, "dbµv/m": 1.0,
    "dbuv": 1.0, "dbµv": 1.0,
    "dbua/m": 1.0, "dbµa/m": 1.0,
    "dbm": None,  # cannot convert without impedance
}
_UNIT_CONVERSIONS_V: dict[str, float] = {"mv": 0.001, "v": 1.0, "kv": 1000.0}
_UNIT_CONVERSIONS_CONC: dict[str, float | None] = {
    "ppm": 1.0, "mg/kg": 1.0, "%": 10000.0,
}
_NUMERIC_TOLERANCE = 0.01  # 1% relative tolerance


def normalize_cell_value(raw: str, column_role: str) -> tuple[float | None, str | None]:
    """Normalize a cell value to a canonical unit for numeric comparison.

    Returns (normalized_float, canonical_unit) or (None, None) when the value
    cannot be converted (incomparable units, non-numeric text, etc.).
    """
    cleaned = raw.strip()
    m = _NUMERIC_UNIT_RE.match(cleaned)
    if not m:
        return None, None
    num_str = m.group(1).replace(",", ".").replace(" ", "").lstrip("<>≤≥")
    unit = m.group(2).lower()
    try:
        num = float(num_str)
    except ValueError:
        return None, None

    if column_role in ("limit", "measured", "margin"):
        # Try EMC dB-family first
        if unit in _UNIT_TO_DB_UV_M:
            factor = _UNIT_TO_DB_UV_M[unit]
            if factor is None:
                return None, None  # incomparable
            return num * factor, "dbuv/m"
        # Safety voltage
        if unit in _UNIT_CONVERSIONS_V:
            return num * _UNIT_CONVERSIONS_V[unit], "v"
        # Environmental concentration
        if unit in _UNIT_CONVERSIONS_CONC:
            factor = _UNIT_CONVERSIONS_CONC[unit]
            if factor is None:
                return None, None
            return num * factor, "mg/kg"
    return num, unit


def cell_values_equivalent(
    left_raw: str, right_raw: str, column_role: str
) -> bool:
    """Return True when two cell values are equivalent after unit normalization.

    Applies 1% relative tolerance for numeric values in the same canonical unit.
    Falls back to normalized text equality when normalization is not possible.
    """
    left_norm, left_unit = normalize_cell_value(left_raw, column_role)
    right_norm, right_unit = normalize_cell_value(right_raw, column_role)
    if left_norm is not None and right_norm is not None and left_unit == right_unit:
        if left_norm == 0 and right_norm == 0:
            return True
        ref = max(abs(left_norm), abs(right_norm))
        return abs(left_norm - right_norm) / ref <= _NUMERIC_TOLERANCE
    return normalize_cell(left_raw) == normalize_cell(right_raw)


def classify_result_transition(left_raw: str, right_raw: str) -> str | None:
    """Classify a PASS/FAIL transition in a result column.

    Returns "pass_to_fail", "fail_to_pass", "unchanged", or None (non-result value).
    """
    left = normalize_cell(left_raw).lower()
    right = normalize_cell(right_raw).lower()
    is_left_result = left in _PASS_FAIL_VALUES
    is_right_result = right in _PASS_FAIL_VALUES
    if not is_left_result or not is_right_result:
        return None
    left_pass = left in _PASS_VALUES
    right_pass = right in _PASS_VALUES
    if left_pass and not right_pass:
        return "pass_to_fail"
    if not left_pass and right_pass:
        return "fail_to_pass"
    return "unchanged"


def row_key_from_values(values: list[str]) -> str:
    meaningful = [value.strip().lower() for value in values if value and value.strip()]
    if not meaningful:
        return ""
    return " | ".join(meaningful[:2])


def row_fingerprint(row_data: dict[str, str]) -> str:
    payload = json.dumps(
        {key: row_data[key] for key in sorted(row_data.keys())},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_table_cells(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for cell in cells:
        normalized.append(
            {
                "row": int(cell.get("row") or 0),
                "col": int(cell.get("col") or 0),
                "row_span": int(cell.get("row_span") or 1),
                "col_span": int(cell.get("col_span") or 1),
                "text": normalize_cell(cell.get("text") or ""),
                "is_header": bool(cell.get("is_header", False)),
            }
        )
    normalized.sort(key=lambda cell: (cell["row"], cell["col"]))
    return normalized


def _detect_header_depth_by_content(cells: list[dict[str, Any]], num_cols: int) -> int:
    """Infer header row count using content analysis when Docling flags are absent.

    A row is treated as a header row when:
    - All columns (or all-but-one) have non-empty text
    - Average cell text length ≤ 35 characters
    - Numeric token density < 0.25 (labels, not data values)

    Returns 1, 2, or 3 (capped at 3 for GRC multi-level test matrices).
    Stops at the first row that fails all three conditions.
    """
    rows_by_idx: dict[int, list[str]] = {}
    for cell in cells:
        r = int(cell.get("row") or 0)
        rows_by_idx.setdefault(r, []).append(str(cell.get("text") or "").strip())

    depth = 0
    for row_idx in sorted(rows_by_idx)[:3]:  # check rows 0, 1, 2 only
        texts = rows_by_idx[row_idx]
        non_empty = [t for t in texts if t]
        if not non_empty:
            break
        coverage = len(non_empty) / max(1, num_cols)
        avg_len = sum(len(t) for t in non_empty) / len(non_empty)
        numeric_count = sum(1 for t in non_empty if any(ch.isdigit() for ch in t))
        numeric_density = numeric_count / len(non_empty)
        is_header_row = coverage >= 0.7 and avg_len <= 35 and numeric_density < 0.25
        if not is_header_row:
            break
        depth += 1

    return max(1, depth)


def extract_headers_from_cells(
    cells: list[dict[str, Any]], num_cols: int
) -> tuple[list[str], int]:
    """Extract column headers, handling multi-row (grouped) header tables.

    Detection priority:
    1. Docling ``column_header`` / ``is_header`` flags (most reliable)
    2. col_span > 1 in row 0 heuristic (original behaviour for spanned headers)
    3. Content-based depth inference (fallback for GRC tables with no flags/spans)

    Returns (headers, header_depth) where header_depth is 1, 2, or 3.
    Empty stub columns (row 0 col N is blank while data rows have values) receive
    a ``_row_label_N`` tag so schema_signature() can exclude them from hashing.
    """
    num_cols = max(0, int(num_cols or 0))
    if not cells or num_cols == 0:
        return [f"column_{c + 1}" for c in range(num_cols)], 1

    # Priority 1: use Docling column_header / is_header flags when available.
    # These are set by granite-docling VLM or table structure model.
    flagged_header_cells = [
        c for c in cells
        if c.get("column_header") or (c.get("is_header") and int(c.get("row") or 0) == 0)
    ]
    if flagged_header_cells:
        header_rows: dict[int, dict[int, str]] = {}
        for c in flagged_header_cells:
            r, col = int(c.get("row") or 0), int(c.get("col") or 0)
            header_rows.setdefault(r, {})[col] = str(c.get("text") or "").strip()
        max_header_row = max(header_rows)
        header_depth = max_header_row + 1
        headers: list[str] = []
        for col in range(num_cols):
            parts = [header_rows.get(r, {}).get(col, "") for r in sorted(header_rows)]
            parts = [p for p in parts if p]
            combined = " ".join(parts) if parts else f"_row_label_{col}"
            headers.append(normalize_header(combined))
        return _fuse_hyphenated_headers(headers), header_depth

    # Priority 2: col_span > 1 in row 0 → multi-row grouped header.
    row0_coverage: dict[int, str] = {}
    for cell in cells:
        if int(cell.get("row") or 0) != 0:
            continue
        col = int(cell.get("col") or 0)
        col_span = int(cell.get("col_span") or 1)
        text = str(cell.get("text") or "")
        for c in range(col, col + col_span):
            row0_coverage[c] = text

    row0_has_spans = any(
        int(cell.get("col_span") or 1) > 1
        for cell in cells
        if int(cell.get("row") or 0) == 0
    )

    # Collect row 1 and row 2 sub-headers when spans are detected.
    row1_coverage: dict[int, str] = {}
    row2_coverage: dict[int, str] = {}
    if row0_has_spans:
        for cell in cells:
            row = int(cell.get("row") or 0)
            col = int(cell.get("col") or 0)
            text = str(cell.get("text") or "").strip()
            if row == 1 and text:
                row1_coverage[col] = text
            elif row == 2 and text:
                row2_coverage[col] = text

    # Priority 3: content-based depth when no spans found.
    if not row0_has_spans:
        content_depth = _detect_header_depth_by_content(cells, num_cols)
        if content_depth >= 2:
            # Re-collect row1 using content depth
            for cell in cells:
                if int(cell.get("row") or 0) == 1:
                    col = int(cell.get("col") or 0)
                    text = str(cell.get("text") or "").strip()
                    if text:
                        row1_coverage[col] = text
            if content_depth >= 3:
                for cell in cells:
                    if int(cell.get("row") or 0) == 2:
                        col = int(cell.get("col") or 0)
                        text = str(cell.get("text") or "").strip()
                        if text:
                            row2_coverage[col] = text

    has_row1 = bool(row1_coverage)
    has_row2 = bool(row2_coverage)
    header_depth = 1 + (1 if has_row1 else 0) + (1 if has_row2 else 0)

    # Identify empty stub columns: row 0 is blank but data rows have content.
    data_rows: dict[int, dict[int, str]] = {}
    for cell in cells:
        r = int(cell.get("row") or 0)
        if r < header_depth:
            continue
        data_rows.setdefault(r, {})[int(cell.get("col") or 0)] = str(cell.get("text") or "").strip()
    stub_cols: set[int] = set()
    for col in range(num_cols):
        if row0_coverage.get(col, "").strip():
            continue
        if any(data_rows.get(r, {}).get(col, "") for r in data_rows):
            stub_cols.add(col)

    headers = []
    for col in range(num_cols):
        row0_text = row0_coverage.get(col, "")
        row1_text = row1_coverage.get(col, "") if has_row1 else ""
        row2_text = row2_coverage.get(col, "") if has_row2 else ""

        parts = [t for t in [row0_text, row1_text, row2_text] if t and t != row0_text or (t == row0_text and not row1_text and not row2_text)]
        # Simpler: combine non-empty distinct parts
        seen: list[str] = []
        for t in [row0_text, row1_text, row2_text]:
            if t and t not in seen:
                seen.append(t)
        combined = " ".join(seen) if seen else ""

        if not combined:
            # Stub column (empty row-0 cell used as row-label margin)
            combined = f"_row_label_{col}" if col in stub_cols else f"column_{col + 1}"

        headers.append(normalize_header(combined))

    headers = _fuse_hyphenated_headers(headers)
    return headers, header_depth


def rows_from_cells(
    cells: list[dict[str, Any]], headers: list[str], *, header_depth: int = 1
) -> list[dict[str, Any]]:
    if not cells:
        return []

    rows_data: dict[int, dict[str, str]] = defaultdict(dict)
    for cell in cells:
        row = int(cell.get("row") or 0)
        col = int(cell.get("col") or 0)
        if row < header_depth:
            continue
        header = headers[col] if col < len(headers) else f"column_{col + 1}"
        # Skip stub columns — they contain row-label text, not entity values
        if header.startswith("_row_label_"):
            continue
        text = str(cell.get("text") or "").strip()
        rows_data[row][header] = text

    rows: list[dict[str, Any]] = []
    for row_index in sorted(rows_data):
        row_data = rows_data[row_index]
        ordered_values = [row_data.get(header, "") for header in headers]
        rows.append(
            {
                "row_index": row_index,
                "row_key": row_key_from_values(ordered_values),
                "row_data": row_data,
                "row_fingerprint": row_fingerprint(row_data),
            }
        )
    return rows


def enrich_table_with_facts(table: Any) -> None:
    """Populate NormalizedFact objects and semantic_key on each TableCell in-place.

    Also stores the detected EMC test type in table.metadata["emc_test_type"].
    This is a post-normalization pass — the table must already have canonical cells.

    Args:
        table: CanonicalTable instance (typed as Any to avoid circular imports at
               module level; the function handles missing attributes gracefully)
    """
    try:
        from grc_policy_server.services.ingestion.ontology.column_mapper import (
            ENTITY_TYPE_DEFAULT_UNIT,
            map_header,
        )
        from grc_policy_server.services.ingestion.ontology.emc_ontology import (
            EMCTestClassifier,
            NormalizedFactExtractor,
            OntologyEntityType,
        )
    except ImportError:
        return  # Ontology module not available — skip enrichment silently

    extractor = NormalizedFactExtractor()
    classifier = EMCTestClassifier()

    caption = getattr(table, "caption_original", "") or getattr(table, "caption_normalized", "") or ""
    columns = getattr(table, "columns", []) or []
    headers = [getattr(c, "name", "") for c in columns]
    section_path = getattr(table, "section_path", []) or []
    table_uid = getattr(table, "table_uid", "") or ""

    # Detect test type
    test_type = classifier.classify_table(caption, headers)
    if test_type.value == "unknown" and section_path:
        test_type = classifier.classify_from_section_path(section_path)

    # Store on metadata (metadata is a mutable dict on CanonicalTable)
    meta = getattr(table, "metadata", {})
    if isinstance(meta, dict):
        meta["emc_test_type"] = test_type.value

    # Build column index → entity type map
    col_entity_map: dict[int, OntologyEntityType] = {}
    for col in columns:
        entity = map_header(getattr(col, "name", ""))
        if entity is not None:
            col_entity_map[getattr(col, "index", 0)] = entity

    # Enrich each cell
    rows = getattr(table, "rows", []) or []
    for row in rows:
        cells = getattr(row, "cells", []) or []
        for cell in cells:
            col_idx = getattr(cell, "col", 0)
            entity_type = col_entity_map.get(col_idx)
            col_name = ""
            if col_idx < len(columns):
                col_name = getattr(columns[col_idx], "name", "")

            cell_text = getattr(cell, "text", "") or ""
            facts = extractor.extract_from_cell(
                cell_text,
                column_name=col_name,
                owner_object_id=table_uid,
            )

            # Column-unit inheritance: bare numeric + typed column → synthesise fact
            if not facts and entity_type is not None:
                inherited_unit = ENTITY_TYPE_DEFAULT_UNIT.get(entity_type)
                if inherited_unit:
                    fact_type_map = {
                        OntologyEntityType.EMISSION_LIMIT: "emission_limit",
                        OntologyEntityType.FIELD_STRENGTH: "field_strength",
                        OntologyEntityType.FREQUENCY_RANGE: "frequency_range",
                    }
                    fact_type = fact_type_map.get(entity_type)
                    if fact_type:
                        facts = extractor.extract_bare_numeric_with_unit(
                            cell_text, inherited_unit, fact_type, owner_object_id=table_uid
                        )

            if facts:
                cell.normalized_facts = facts

            # Populate semantic_key from entity type
            if entity_type is not None and not getattr(cell, "semantic_key", ""):
                cell.semantic_key = entity_type.value


def table_text_projection(
    table_title: str,
    headers: list[str],
    rows: list[dict[str, str]],
    *,
    max_rows: int = 50,
) -> str:
    parts: list[str] = []
    if table_title:
        parts.append(f"table: {table_title}")

    if headers:
        parts.append("columns: " + " | ".join(headers))

    for row in rows[:max_rows]:
        row_items = [f"{key}: {value}" for key, value in row.items() if value.strip()]
        if row_items:
            parts.append(" ; ".join(row_items))

    return "\n".join(parts).strip()
