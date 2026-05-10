from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from typing import Any

from grc_policy_server.utils.hashing import normalize_for_comparison


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
    canonical = " | ".join(normalize_table_headers(headers))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def extract_headers_from_cells(
    cells: list[dict[str, Any]], num_cols: int
) -> tuple[list[str], int]:
    """Extract column headers, handling multi-row (grouped) header tables.

    Returns (headers, header_depth) where header_depth is 1 for single-row
    headers and 2 when row 1 sub-headers were used to fill col_span gaps.
    """
    num_cols = max(0, int(num_cols or 0))
    if not cells or num_cols == 0:
        return [f"column_{c + 1}" for c in range(num_cols)], 1

    # Row 0: expand col_span so every covered column position gets the group text
    row0_coverage: dict[int, str] = {}
    for cell in cells:
        if int(cell.get("row") or 0) != 0:
            continue
        col = int(cell.get("col") or 0)
        col_span = int(cell.get("col_span") or 1)
        text = str(cell.get("text") or "")
        for c in range(col, col + col_span):
            row0_coverage[c] = text

    # Detect multi-row header: row 0 has at least one cell with col_span > 1
    row0_has_spans = any(
        int(cell.get("col_span") or 1) > 1
        for cell in cells
        if int(cell.get("row") or 0) == 0
    )
    # Collect row 1 sub-header texts (only when spans detected)
    row1_coverage: dict[int, str] = {}
    if row0_has_spans:
        for cell in cells:
            if int(cell.get("row") or 0) != 1:
                continue
            col = int(cell.get("col") or 0)
            text = str(cell.get("text") or "").strip()
            if text:
                row1_coverage[col] = text

    use_subheaders = bool(row1_coverage)
    header_depth = 2 if use_subheaders else 1

    headers: list[str] = []
    for col in range(num_cols):
        row0_text = row0_coverage.get(col, "")
        row1_text = row1_coverage.get(col, "") if use_subheaders else ""

        if row0_text and row1_text and row0_text != row1_text:
            combined = f"{row0_text} {row1_text}"
        elif row1_text:
            combined = row1_text
        elif row0_text:
            combined = row0_text
        else:
            combined = f"column_{col + 1}"

        headers.append(normalize_header(combined))
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
