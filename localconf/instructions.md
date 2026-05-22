## Prevent insignificant data for ingestion
```.py
import unicodedata
from typing import Iterable

TOC_TITLE_RE = re.compile(
    r"^\s*(table of contents|contents|index|glossary|list of figures|list of tables)\s*$",
    re.I,
)
def looks_like_auxiliary(text: str, section_title: str) -> bool:
    """
    Filter TOC / Index / similar junk.
    """
    t = (text or "").strip()
    if not t:
        return True

    if section_title and TOC_TITLE_RE.match(section_title.strip()):
        return True

    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    dot_leaders = len(re.findall(r"\.{3,}", t))
    trailing_page_numbers = sum(1 for ln in lines if re.search(r"\s\d{1,4}\s*$", ln))
    short_lines = sum(1 for ln in lines if len(ln) <= 70)

    return (
        len(lines) >= 6
        and dot_leaders >= 3
        and trailing_page_numbers >= 3
        and short_lines / max(len(lines), 1) > 0.6
    )
```
## table preprocessing

```table_normalize.py
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from normalize import canonicalize_text


def normalize_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return canonicalize_text(text).strip()


def normalize_header(value: Any) -> str:
    text = normalize_cell(value).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_table_headers(headers: list[str]) -> list[str]:
    return [normalize_header(h) for h in headers]


def schema_signature(headers: list[str]) -> str:
    canon = " | ".join(normalize_table_headers(headers))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def row_key_from_values(values: list[str]) -> str:
    """
    Prefer first meaningful non-empty columns as synthetic row anchor.
    """
    meaningful = [v.strip().lower() for v in values if v and v.strip()]
    if not meaningful:
        return ""
    return " | ".join(meaningful[:2])


def row_fingerprint(row_data: dict[str, str]) -> str:
    payload = json.dumps(
        {k: row_data[k] for k in sorted(row_data.keys())},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def table_text_projection(
    table_title: str,
    headers: list[str],
    rows: list[dict[str, str]],
    max_rows: int = 50,
) -> str:
    """
    Text form used for embedding and semantic retrieval.
    Keeps table meaning while discarding layout junk.
    """
    parts: list[str] = []
    if table_title:
        parts.append(f"Table: {table_title}")

    if headers:
        parts.append("Columns: " + " | ".join(headers))

    for row in rows[:max_rows]:
        row_items = [f"{k}: {v}" for k, v in row.items() if v.strip()]
        if row_items:
            parts.append(" ; ".join(row_items))

    return "\n".join(parts).strip()

```

## table comparision

```compare_tables.py
from __future__ import annotations

import math
from typing import Any, Optional

from config import settings
from ollama_client import OllamaClient
from table_normalize import normalize_cell


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def table_section_key(section_path: str, section_title: str, table_title: str) -> str:
    return f"{(section_path or '').strip()}|{(section_title or '').strip().lower()}|{(table_title or '').strip().lower()}"


def render_table_citation(table: dict[str, Any]) -> str:
    doc_id = table["doc_id"]
    section_path = table.get("section_path") or "(unknown)"
    section_title = table.get("section_title") or "(untitled)"
    table_title = table.get("table_title") or "(untitled table)"
    ps = table.get("page_start")
    pe = table.get("page_end")
    if ps is None:
        page = ""
    elif pe and pe != ps:
        page = f", pp. {ps}–{pe}"
    else:
        page = f", p. {ps}"
    return f'{doc_id} §{section_path} "{section_title}" / {table_title}{page}'


def table_repr_vector(table: dict[str, Any]) -> list[float]:
    return table.get("_vector") or []


def align_tables(
    old_tables: list[dict[str, Any]],
    new_tables: list[dict[str, Any]],
    min_similarity: float = 0.82,
) -> dict[str, Optional[str]]:
    """
    Map new_table_id -> old_table_id | None
    """
    old_by_id = {t["table_id"]: t for t in old_tables}
    new_by_id = {t["table_id"]: t for t in new_tables}

    used_old: set[str] = set()
    mapping: dict[str, Optional[str]] = {}

    for new_table in new_tables:
        new_id = new_table["table_id"]

        # 1) strong anchors: same section path + same schema signature
        best_old: Optional[str] = None
        for old_table in old_tables:
            if old_table["table_id"] in used_old:
                continue
            same_path = (old_table.get("section_path") or "") == (
                new_table.get("section_path") or ""
            )
            same_schema = (old_table.get("schema_signature") or "") == (
                new_table.get("schema_signature") or ""
            )
            if same_path and same_schema:
                best_old = old_table["table_id"]
                break

        if best_old:
            mapping[new_id] = best_old
            used_old.add(best_old)
            continue

        # 2) embedding fallback
        best_score = 0.0
        best_old = None
        for old_table in old_tables:
            if old_table["table_id"] in used_old:
                continue
            score = cosine_similarity(
                table_repr_vector(new_table), table_repr_vector(old_table)
            )
            if score > best_score:
                best_score = score
                best_old = old_table["table_id"]

        if best_old and best_score >= min_similarity:
            mapping[new_id] = best_old
            used_old.add(best_old)
        else:
            mapping[new_id] = None

    return mapping


def rows_to_maps(
    table: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = table.get("rows_json") or []
    by_key: dict[str, dict[str, Any]] = {}
    by_fp: dict[str, dict[str, Any]] = {}

    for row in rows:
        key = row.get("row_key") or ""
        fp = row.get("row_fingerprint") or ""
        if key and key not in by_key:
            by_key[key] = row
        if fp and fp not in by_fp:
            by_fp[fp] = row

    return by_key, by_fp


def compare_row_cells(
    old_row: dict[str, Any], new_row: dict[str, Any]
) -> list[dict[str, Any]]:
    old_data = old_row.get("row_data") or {}
    new_data = new_row.get("row_data") or {}

    keys = sorted(set(old_data.keys()) | set(new_data.keys()))
    changes: list[dict[str, Any]] = []

    for key in keys:
        old_val = normalize_cell(old_data.get(key, ""))
        new_val = normalize_cell(new_data.get(key, ""))

        if old_val == new_val:
            continue

        changes.append(
            {
                "column": key,
                "old": old_val,
                "new": new_val,
            }
        )

    return changes


def compare_tables_structured(
    old_table: dict[str, Any], new_table: dict[str, Any]
) -> dict[str, Any]:
    old_by_key, old_by_fp = rows_to_maps(old_table)
    new_by_key, new_by_fp = rows_to_maps(new_table)

    old_rows = old_table.get("rows_json") or []
    new_rows = new_table.get("rows_json") or []

    matched_old_row_ids: set[int] = set()
    matched_new_row_ids: set[int] = set()

    modified_rows: list[dict[str, Any]] = []
    added_rows: list[dict[str, Any]] = []
    removed_rows: list[dict[str, Any]] = []

    # pass 1: exact row fingerprint match => unchanged
    old_fp_to_index = {row.get("row_fingerprint"): i for i, row in enumerate(old_rows)}
    for j, new_row in enumerate(new_rows):
        fp = new_row.get("row_fingerprint")
        if fp in old_fp_to_index:
            matched_old_row_ids.add(old_fp_to_index[fp])
            matched_new_row_ids.add(j)

    # pass 2: row_key match => compare cells
    old_key_to_index = {
        row.get("row_key"): i
        for i, row in enumerate(old_rows)
        if i not in matched_old_row_ids and (row.get("row_key") or "")
    }

    for j, new_row in enumerate(new_rows):
        if j in matched_new_row_ids:
            continue
        key = new_row.get("row_key") or ""
        if key and key in old_key_to_index:
            i = old_key_to_index[key]
            old_row = old_rows[i]
            changes = compare_row_cells(old_row, new_row)
            matched_old_row_ids.add(i)
            matched_new_row_ids.add(j)

            if changes:
                modified_rows.append(
                    {
                        "row_key": key,
                        "old_row_text": old_row.get("row_text", ""),
                        "new_row_text": new_row.get("row_text", ""),
                        "cell_changes": changes,
                    }
                )

    # remaining
    for i, old_row in enumerate(old_rows):
        if i not in matched_old_row_ids:
            removed_rows.append(old_row)

    for j, new_row in enumerate(new_rows):
        if j not in matched_new_row_ids:
            added_rows.append(new_row)

    schema_changed = (old_table.get("schema_signature") or "") != (
        new_table.get("schema_signature") or ""
    )

    return {
        "schema_changed": schema_changed,
        "old_headers": old_table.get("headers") or [],
        "new_headers": new_table.get("headers") or [],
        "modified_rows": modified_rows,
        "added_rows": added_rows,
        "removed_rows": removed_rows,
    }


class TableComparisonService:
    def __init__(self) -> None:
        self.ollama = OllamaClient()

    def summarize_table_change(
        self, old_table: dict[str, Any], new_table: dict[str, Any], diff: dict[str, Any]
    ) -> dict[str, Any]:
        old_bits = []
        new_bits = []

        for row in diff["removed_rows"][:8]:
            old_bits.append(
                {
                    "citation": render_table_citation(old_table),
                    "text": row.get("row_text", ""),
                }
            )

        for row in diff["added_rows"][:8]:
            new_bits.append(
                {
                    "citation": render_table_citation(new_table),
                    "text": row.get("row_text", ""),
                }
            )

        for row in diff["modified_rows"][:8]:
            old_bits.append(
                {
                    "citation": render_table_citation(old_table),
                    "text": row.get("old_row_text", ""),
                }
            )
            new_bits.append(
                {
                    "citation": render_table_citation(new_table),
                    "text": row.get("new_row_text", ""),
                }
            )

        if diff["schema_changed"]:
            old_bits.append(
                {
                    "citation": render_table_citation(old_table),
                    "text": "Headers: " + " | ".join(diff["old_headers"]),
                }
            )
            new_bits.append(
                {
                    "citation": render_table_citation(new_table),
                    "text": "Headers: " + " | ".join(diff["new_headers"]),
                }
            )

        return self.ollama.summarize_change(old_bits, new_bits)

```


## table ingestion

```table_ingest.py
from __future__ import annotations

from typing import Any, List, Optional, Tuple

import pandas as pd
from config import settings
from docling.chunking import HierarchicalChunker
from docling.document_converter import DocumentConverter
from models import ChunkRecord
from models_tables import TableRecord
from normalize import canonicalize_text, looks_like_auxiliary, sha256_hex, simhash64
from ollama_client import OllamaClient
from table_normalize import (
    normalize_cell,
    normalize_header,
    normalize_table_headers,
    row_fingerprint,
    row_key_from_values,
    schema_signature,
    table_text_projection,
)
from weaviate_store import WeaviateStore


def _try_get(obj: Any, *keys: str, default=None):
    current = obj
    for key in keys:
        if current is None:
            return default
        if hasattr(current, key):
            current = getattr(current, key)
        elif isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current


def extract_text(chunk: Any) -> str:
    for key in ("text", "content", "document_text"):
        value = _try_get(chunk, key)
        if isinstance(value, str) and value.strip():
            return value
    return str(chunk)


def extract_pages(node: Any) -> tuple[Optional[int], Optional[int]]:
    meta = _try_get(node, "meta") or _try_get(node, "metadata") or {}
    page_start = (
        _try_get(meta, "page_start")
        or _try_get(meta, "pageStart")
        or _try_get(meta, "page")
    )
    page_end = _try_get(meta, "page_end") or _try_get(meta, "pageEnd") or page_start

    if page_start is None:
        prov = _try_get(meta, "provenance")
        if isinstance(prov, list) and prov:
            page_start = _try_get(prov[0], "page_no") or _try_get(prov[0], "page")
            page_end = (
                _try_get(prov[-1], "page_no")
                or _try_get(prov[-1], "page")
                or page_start
            )

    try:
        page_start = int(page_start) if page_start is not None else None
    except Exception:
        page_start = None

    try:
        page_end = int(page_end) if page_end is not None else page_start
    except Exception:
        page_end = page_start

    return page_start, page_end


def extract_section(node: Any) -> tuple[str, str]:
    meta = _try_get(node, "meta") or _try_get(node, "metadata") or {}
    headers = (
        _try_get(meta, "headers")
        or _try_get(meta, "headings")
        or _try_get(meta, "header_chain")
        or []
    )

    section_title = ""
    if isinstance(headers, list) and headers:
        last = headers[-1]
        section_title = (
            _try_get(last, "text") or _try_get(last, "title") or str(last)
        ).strip()
    else:
        section_title = (
            _try_get(meta, "section_title") or _try_get(meta, "title") or ""
        ).strip()

    section_path = (
        _try_get(meta, "section_path") or _try_get(meta, "section_number") or ""
    ).strip()

    import re

    match = re.match(r"^\s*((\d+(\.\d+){0,8}))\s+(.+)$", section_title)
    if match:
        section_path = section_path or match.group(1)
        section_title = match.group(4).strip()

    return section_path, section_title


def extract_table_title(table_obj: Any, index: int) -> str:
    candidates = [
        _try_get(table_obj, "caption"),
        _try_get(table_obj, "title"),
        _try_get(table_obj, "name"),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return f"Table {index + 1}"


def table_to_rows(table_obj: Any) -> tuple[list[str], list[dict[str, str]], str]:
    """
    Converts Docling table -> dataframe -> normalized row dicts + markdown.
    """
    df: pd.DataFrame = table_obj.export_to_dataframe()

    # Make headers stable
    raw_headers = [str(col) if col is not None else "" for col in df.columns.tolist()]
    headers = normalize_table_headers(raw_headers)

    rows: list[dict[str, str]] = []
    for _, row in df.iterrows():
        row_dict: dict[str, str] = {}
        for raw_h, canon_h, value in zip(raw_headers, headers, row.tolist()):
            key = canon_h or normalize_header(raw_h) or "column"
            row_dict[key] = normalize_cell(value)
        rows.append(row_dict)

    markdown = df.to_markdown(index=False)
    return headers, rows, markdown


class IngestionService:
    def __init__(self) -> None:
        self.ollama = OllamaClient()

    def parse_chunks(
        self, document: Any, doc_id: str, doc_version: int
    ) -> List[ChunkRecord]:
        chunker = HierarchicalChunker()
        raw_chunks = list(chunker.chunk(document))

        prepared: list[dict] = []
        for index, chunk in enumerate(raw_chunks):
            text_raw = extract_text(chunk)
            text_canonical = canonicalize_text(text_raw)
            section_path, section_title = extract_section(chunk)
            page_start, page_end = extract_pages(chunk)
            is_aux = looks_like_auxiliary(text_raw, section_title)

            if is_aux or len(text_canonical) < settings.min_chunk_chars:
                continue

            prepared.append(
                {
                    "doc_id": doc_id,
                    "doc_version": doc_version,
                    "chunk_id": f"{doc_id}:{index}",
                    "chunk_index": index,
                    "section_path": section_path,
                    "section_title": section_title,
                    "text_raw": text_raw,
                    "text_canonical": text_canonical,
                    "page_start": page_start,
                    "page_end": page_end,
                    "is_auxiliary": False,
                    "sha256": sha256_hex(text_canonical),
                    "simhash64": str(simhash64(text_canonical)),
                }
            )

        vectors = (
            self.ollama.embed_texts([item["text_canonical"] for item in prepared])
            if prepared
            else []
        )

        out: list[ChunkRecord] = []
        for item, vector in zip(prepared, vectors):
            out.append(
                ChunkRecord(
                    doc_id=item["doc_id"],
                    doc_version=item["doc_version"],
                    chunk_id=item["chunk_id"],
                    chunk_index=item["chunk_index"],
                    section_path=item["section_path"],
                    section_title=item["section_title"],
                    text_raw=item["text_raw"],
                    text_canonical=item["text_canonical"],
                    page_start=item["page_start"],
                    page_end=item["page_end"],
                    is_auxiliary=False,
                    sha256=item["sha256"],
                    simhash64=item["simhash64"],
                    vector=vector,
                )
            )
        return out

    def parse_tables(
        self, document: Any, doc_id: str, doc_version: int
    ) -> List[TableRecord]:
        tables = list(_try_get(document, "tables", default=[]) or [])

        prepared: list[dict] = []
        for index, table_obj in enumerate(tables):
            try:
                headers, rows, markdown = table_to_rows(table_obj)
            except Exception:
                continue

            if not headers and not rows:
                continue

            row_count = len(rows)
            col_count = len(headers)

            # one-column tables can be semantically important, but Docling chunker has had edge cases around them;
            # here we ingest them directly from document.tables instead of relying on chunker serialization.
            table_title = extract_table_title(table_obj, index)
            section_path, section_title = extract_section(table_obj)
            page_start, page_end = extract_pages(table_obj)

            projection = table_text_projection(
                table_title=table_title,
                headers=headers,
                rows=rows,
            )

            if len(projection.strip()) < settings.min_chunk_chars:
                continue

            rows_json: list[dict[str, Any]] = []
            for row_index, row_data in enumerate(rows):
                values = [row_data.get(h, "") for h in headers]
                row_key = row_key_from_values(values)
                rows_json.append(
                    {
                        "row_index": row_index,
                        "row_key": row_key,
                        "row_fingerprint": row_fingerprint(row_data),
                        "row_text": " ; ".join(
                            f"{k}: {v}" for k, v in row_data.items() if v.strip()
                        ),
                        "row_data": row_data,
                    }
                )

            prepared.append(
                {
                    "doc_id": doc_id,
                    "doc_version": doc_version,
                    "table_id": f"{doc_id}:table:{index}",
                    "table_index": index,
                    "section_path": section_path,
                    "section_title": section_title,
                    "page_start": page_start,
                    "page_end": page_end,
                    "table_title": table_title,
                    "headers": headers,
                    "schema_signature": schema_signature(headers),
                    "table_markdown": markdown,
                    "table_text_projection": projection,
                    "row_count": row_count,
                    "col_count": col_count,
                    "rows_json": rows_json,
                }
            )

        vectors = (
            self.ollama.embed_texts(
                [item["table_text_projection"] for item in prepared]
            )
            if prepared
            else []
        )

        out: list[TableRecord] = []
        for item, vector in zip(prepared, vectors):
            out.append(
                TableRecord(
                    doc_id=item["doc_id"],
                    doc_version=item["doc_version"],
                    table_id=item["table_id"],
                    table_index=item["table_index"],
                    section_path=item["section_path"],
                    section_title=item["section_title"],
                    page_start=item["page_start"],
                    page_end=item["page_end"],
                    table_title=item["table_title"],
                    headers=item["headers"],
                    schema_signature=item["schema_signature"],
                    table_markdown=item["table_markdown"],
                    table_text_projection=item["table_text_projection"],
                    row_count=item["row_count"],
                    col_count=item["col_count"],
                    rows_json=item["rows_json"],
                    vector=vector,
                )
            )
        return out

    def ingest_file(
        self, file_path: str, doc_id: str, doc_version: int
    ) -> dict[str, int]:
        converter = DocumentConverter()
        result = converter.convert(file_path)
        document = _try_get(result, "document") or result

        store = WeaviateStore()
        try:
            store.ensure_schema()
            chunks = self.parse_chunks(
                document=document, doc_id=doc_id, doc_version=doc_version
            )
            tables = self.parse_tables(
                document=document, doc_id=doc_id, doc_version=doc_version
            )
            store.upsert_chunks(chunks)
            store.upsert_tables(tables)
            return {"chunks": len(chunks), "tables": len(tables)}
        finally:
            store.close()
```

## table normalization before ingestion
```table_normalze.py
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from normalize import canonicalize_text


def normalize_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return canonicalize_text(text).strip()


def normalize_header(value: Any) -> str:
    text = normalize_cell(value).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_table_headers(headers: list[str]) -> list[str]:
    return [normalize_header(h) for h in headers]


def schema_signature(headers: list[str]) -> str:
    canon = " | ".join(normalize_table_headers(headers))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def row_key_from_values(values: list[str]) -> str:
    """
    Prefer first meaningful non-empty columns as synthetic row anchor.
    """
    meaningful = [v.strip().lower() for v in values if v and v.strip()]
    if not meaningful:
        return ""
    return " | ".join(meaningful[:2])


def row_fingerprint(row_data: dict[str, str]) -> str:
    payload = json.dumps(
        {k: row_data[k] for k in sorted(row_data.keys())},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def table_text_projection(
    table_title: str,
    headers: list[str],
    rows: list[dict[str, str]],
    max_rows: int = 50,
) -> str:
    """
    Text form used for embedding and semantic retrieval.
    Keeps table meaning while discarding layout junk.
    """
    parts: list[str] = []
    if table_title:
        parts.append(f"Table: {table_title}")

    if headers:
        parts.append("Columns: " + " | ".join(headers))

    for row in rows[:max_rows]:
        row_items = [f"{k}: {v}" for k, v in row.items() if v.strip()]
        if row_items:
            parts.append(" ; ".join(row_items))

    return "\n".join(parts).strip()

```

## compare

```compare.py
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from models import Citation, ModifiedPair, SectionComparison
from normalize import hamming64
from ollama_client import OllamaClient
from weaviate_store import WeaviateStore


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def section_key(section_path: str, section_title: str) -> str:
    return f"{(section_path or '').strip()}|{(section_title or '').strip().lower()}"


def chunk_to_citation(chunk: dict[str, Any]) -> Citation:
    return Citation(
        doc_id=chunk["doc_id"],
        section_path=chunk.get("section_path") or "",
        section_title=chunk.get("section_title") or "",
        page_start=chunk.get("page_start"),
        page_end=chunk.get("page_end"),
    )


def group_chunks_by_section(
    chunks: List[dict[str, Any]],
) -> Dict[str, List[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        key = section_key(chunk.get("section_path", ""), chunk.get("section_title", ""))
        groups[key].append(chunk)
    return dict(groups)


def mean_vector(vectors: List[List[float]]) -> List[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    out = [0.0] * dim
    for vec in vectors:
        for i, value in enumerate(vec):
            out[i] += value
    return [value / len(vectors) for value in out]


def section_representative(chunks: List[dict[str, Any]]) -> tuple[str, List[float]]:
    text = "\n\n".join(chunk["text_canonical"] for chunk in chunks)[:4000]
    vector = mean_vector([chunk["_vector"] for chunk in chunks if chunk.get("_vector")])
    return text, vector


def align_sections(
    old_sections: Dict[str, List[dict[str, Any]]],
    new_sections: Dict[str, List[dict[str, Any]]],
) -> Dict[str, Optional[str]]:
    """
    Map new_section_key -> old_section_key | None
    """
    old_repr = {
        key: section_representative(chunks) for key, chunks in old_sections.items()
    }
    new_repr = {
        key: section_representative(chunks) for key, chunks in new_sections.items()
    }

    mapping: Dict[str, Optional[str]] = {}
    used_old: set[str] = set()

    for new_key in new_sections.keys():
        new_path, new_title = new_key.split("|", 1)

        # 1. Strong anchor: same section path
        exact_path_candidates = []
        for old_key in old_sections.keys():
            if old_key in used_old:
                continue
            old_path, _ = old_key.split("|", 1)
            if new_path and old_path and new_path == old_path:
                exact_path_candidates.append(old_key)

        if exact_path_candidates:
            selected = exact_path_candidates[0]
            mapping[new_key] = selected
            used_old.add(selected)
            continue

        # 2. Embedding fallback for renumbered / renamed sections
        _, new_vec = new_repr[new_key]
        best_score = 0.0
        best_old_key: Optional[str] = None

        for old_key, (_, old_vec) in old_repr.items():
            if old_key in used_old:
                continue
            score = cosine_similarity(new_vec, old_vec)
            if score > best_score:
                best_score = score
                best_old_key = old_key

        if best_old_key and best_score >= settings.embed_section_min_similarity:
            mapping[new_key] = best_old_key
            used_old.add(best_old_key)
        else:
            mapping[new_key] = None

    return mapping


def diff_aligned_section(
    old_chunks: List[dict[str, Any]],
    new_chunks: List[dict[str, Any]],
) -> tuple[list[dict], list[ModifiedPair], list[dict], list[dict]]:
    """
    Returns:
    unchanged_chunks, modified_pairs, added_chunks, removed_chunks
    """
    old_by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in old_chunks:
        old_by_hash[chunk["sha256"]].append(chunk)

    unchanged_old_ids: set[str] = set()
    unchanged_new_ids: set[str] = set()

    # Pass 1: exact canonical match
    for new_chunk in new_chunks:
        candidates = old_by_hash.get(new_chunk["sha256"], [])
        if candidates:
            old_chunk = candidates.pop()
            unchanged_old_ids.add(old_chunk["chunk_id"])
            unchanged_new_ids.add(new_chunk["chunk_id"])

    old_remaining = [c for c in old_chunks if c["chunk_id"] not in unchanged_old_ids]
    new_remaining = [c for c in new_chunks if c["chunk_id"] not in unchanged_new_ids]

    modified_pairs: list[ModifiedPair] = []
    used_old_ids: set[str] = set()

    # Pass 2: near duplicate via SimHash
    for new_chunk in new_remaining:
        new_fp = int(new_chunk["simhash64"])
        best_distance = 999
        best_old: Optional[dict[str, Any]] = None

        for old_chunk in old_remaining:
            if old_chunk["chunk_id"] in used_old_ids:
                continue
            old_fp = int(old_chunk["simhash64"])
            distance = hamming64(new_fp, old_fp)
            if distance < best_distance:
                best_distance = distance
                best_old = old_chunk

        if best_old and best_distance <= settings.simhash_max_hamming:
            used_old_ids.add(best_old["chunk_id"])
            modified_pairs.append(
                ModifiedPair(
                    old_chunk_id=best_old["chunk_id"],
                    new_chunk_id=new_chunk["chunk_id"],
                    reason="simhash",
                    score=float(best_distance),
                    old_sample=best_old["text_canonical"][:240],
                    new_sample=new_chunk["text_canonical"][:240],
                    old_citation=chunk_to_citation(best_old).render(),
                    new_citation=chunk_to_citation(new_chunk).render(),
                )
            )

    paired_old_ids = {pair.old_chunk_id for pair in modified_pairs}
    paired_new_ids = {pair.new_chunk_id for pair in modified_pairs}

    old_remaining_2 = [c for c in old_remaining if c["chunk_id"] not in paired_old_ids]
    new_remaining_2 = [c for c in new_remaining if c["chunk_id"] not in paired_new_ids]

    # Pass 3: semantic fallback
    used_old_ids_2: set[str] = set()
    for new_chunk in new_remaining_2:
        best_score = 0.0
        best_old: Optional[dict[str, Any]] = None

        for old_chunk in old_remaining_2:
            if old_chunk["chunk_id"] in used_old_ids_2:
                continue
            score = cosine_similarity(new_chunk["_vector"], old_chunk["_vector"])
            if score > best_score:
                best_score = score
                best_old = old_chunk

        if best_old and best_score >= settings.embed_chunk_min_similarity:
            used_old_ids_2.add(best_old["chunk_id"])
            modified_pairs.append(
                ModifiedPair(
                    old_chunk_id=best_old["chunk_id"],
                    new_chunk_id=new_chunk["chunk_id"],
                    reason="embedding",
                    score=best_score,
                    old_sample=best_old["text_canonical"][:240],
                    new_sample=new_chunk["text_canonical"][:240],
                    old_citation=chunk_to_citation(best_old).render(),
                    new_citation=chunk_to_citation(new_chunk).render(),
                )
            )

    all_paired_old = {pair.old_chunk_id for pair in modified_pairs}
    all_paired_new = {pair.new_chunk_id for pair in modified_pairs}

    added_chunks = [
        c
        for c in new_chunks
        if c["chunk_id"] not in unchanged_new_ids
        and c["chunk_id"] not in all_paired_new
    ]
    removed_chunks = [
        c
        for c in old_chunks
        if c["chunk_id"] not in unchanged_old_ids
        and c["chunk_id"] not in all_paired_old
    ]
    unchanged_chunks = [c for c in new_chunks if c["chunk_id"] in unchanged_new_ids]

    return unchanged_chunks, modified_pairs, added_chunks, removed_chunks


class ComparisonService:
    def __init__(self) -> None:
        self.ollama = OllamaClient()

    def compare_docs(self, old_doc_id: str, new_doc_id: str) -> dict[str, Any]:
        store = WeaviateStore()
        try:
            old_chunks = store.fetch_doc_chunks(old_doc_id)
            new_chunks = store.fetch_doc_chunks(new_doc_id)
        finally:
            store.close()

        old_sections = group_chunks_by_section(old_chunks)
        new_sections = group_chunks_by_section(new_chunks)

        alignment = align_sections(old_sections, new_sections)

        results: list[SectionComparison] = []
        used_old_sections: set[str] = set()

        for new_section_key, old_section_key in alignment.items():
            new_section_chunks = new_sections[new_section_key]

            if old_section_key is None:
                new_citations = sorted(
                    {chunk_to_citation(c).render() for c in new_section_chunks}
                )
                results.append(
                    SectionComparison(
                        diff_type="added",
                        old_section_key=None,
                        new_section_key=new_section_key,
                        old_citations=[],
                        new_citations=new_citations,
                        modified_pairs=[],
                        added_chunks=[
                            {
                                "sample": c["text_canonical"][:240],
                                "citation": chunk_to_citation(c).render(),
                            }
                            for c in new_section_chunks[:20]
                        ],
                        removed_chunks=[],
                    )
                )
                continue

            used_old_sections.add(old_section_key)
            old_section_chunks = old_sections[old_section_key]

            _, modified_pairs, added_chunks, removed_chunks = diff_aligned_section(
                old_chunks=old_section_chunks,
                new_chunks=new_section_chunks,
            )

            if not modified_pairs and not added_chunks and not removed_chunks:
                results.append(
                    SectionComparison(
                        diff_type="unchanged",
                        old_section_key=old_section_key,
                        new_section_key=new_section_key,
                        old_citations=sorted(
                            {chunk_to_citation(c).render() for c in old_section_chunks}
                        ),
                        new_citations=sorted(
                            {chunk_to_citation(c).render() for c in new_section_chunks}
                        ),
                        modified_pairs=[],
                        added_chunks=[],
                        removed_chunks=[],
                    )
                )
                continue

            results.append(
                SectionComparison(
                    diff_type="modified",
                    old_section_key=old_section_key,
                    new_section_key=new_section_key,
                    old_citations=sorted(
                        {chunk_to_citation(c).render() for c in old_section_chunks}
                    ),
                    new_citations=sorted(
                        {chunk_to_citation(c).render() for c in new_section_chunks}
                    ),
                    modified_pairs=modified_pairs,
                    added_chunks=[
                        {
                            "sample": c["text_canonical"][:240],
                            "citation": chunk_to_citation(c).render(),
                        }
                        for c in added_chunks[:20]
                    ],
                    removed_chunks=[
                        {
                            "sample": c["text_canonical"][:240],
                            "citation": chunk_to_citation(c).render(),
                        }
                        for c in removed_chunks[:20]
                    ],
                )
            )

        # Old sections that did not map anywhere -> removed
        for old_section_key, old_section_chunks in old_sections.items():
            if old_section_key in used_old_sections:
                continue
            results.append(
                SectionComparison(
                    diff_type="removed",
                    old_section_key=old_section_key,
                    new_section_key=None,
                    old_citations=sorted(
                        {chunk_to_citation(c).render() for c in old_section_chunks}
                    ),
                    new_citations=[],
                    modified_pairs=[],
                    added_chunks=[],
                    removed_chunks=[
                        {
                            "sample": c["text_canonical"][:240],
                            "citation": chunk_to_citation(c).render(),
                        }
                        for c in old_section_chunks[:20]
                    ],
                )
            )

        # Optional Granite summary only for modified sections
        summaries: list[dict[str, Any]] = []
        for section in results:
            if section.diff_type != "modified":
                continue

            old_payload = [
                {"citation": c, "text": ""} for c in section.old_citations[:5]
            ]
            new_payload = [
                {"citation": c, "text": ""} for c in section.new_citations[:5]
            ]

            # Better prompt payload: changed snippets only
            old_changed = [
                {"citation": item["citation"], "text": item["sample"]}
                for item in section.removed_chunks[:8]
            ]
            new_changed = [
                {"citation": item["citation"], "text": item["sample"]}
                for item in section.added_chunks[:8]
            ]

            for pair in section.modified_pairs[:8]:
                old_changed.append(
                    {"citation": pair.old_citation, "text": pair.old_sample}
                )
                new_changed.append(
                    {"citation": pair.new_citation, "text": pair.new_sample}
                )

            summary = self.ollama.summarize_change(
                old_texts=old_changed or old_payload,
                new_texts=new_changed or new_payload,
            )

            summaries.append(
                {
                    "old_section_key": section.old_section_key,
                    "new_section_key": section.new_section_key,
                    "summary": summary,
                }
            )

        return {
            "old_doc_id": old_doc_id,
            "new_doc_id": new_doc_id,
            "sections": results,
            "granite_summaries": summaries,
        }

```

## weaviate store

```weaviate_store.py
from __future__ import annotations

from typing import Any, List

import weaviate
from config import settings
from models import ChunkRecord
from models_tables import TableRecord
from weaviate.classes.config import Configure, DataType, Property
from weaviate.classes.query import Filter


class WeaviateStore:
    CHUNK_COLLECTION = "DocChunk"
    TABLE_COLLECTION = "DocTable"

    def __init__(self) -> None:
        self.client = weaviate.connect_to_custom(
            http_host=settings.weaviate_http_host,
            http_port=settings.weaviate_http_port,
            http_secure=False,
            grpc_host=settings.weaviate_grpc_host,
            grpc_port=settings.weaviate_grpc_port,
            grpc_secure=False,
        )

    def close(self) -> None:
        self.client.close()

    def ensure_schema(self) -> None:
        if not self.client.collections.exists(self.CHUNK_COLLECTION):
            self.client.collections.create(
                name=self.CHUNK_COLLECTION,
                vector_config=Configure.Vectors.self_provided(),
                properties=[
                    Property(name="doc_id", data_type=DataType.TEXT),
                    Property(name="doc_version", data_type=DataType.INT),
                    Property(name="chunk_id", data_type=DataType.TEXT),
                    Property(name="chunk_index", data_type=DataType.INT),
                    Property(name="section_path", data_type=DataType.TEXT),
                    Property(name="section_title", data_type=DataType.TEXT),
                    Property(name="text_raw", data_type=DataType.TEXT),
                    Property(name="text_canonical", data_type=DataType.TEXT),
                    Property(name="page_start", data_type=DataType.INT),
                    Property(name="page_end", data_type=DataType.INT),
                    Property(name="is_auxiliary", data_type=DataType.BOOL),
                    Property(name="sha256", data_type=DataType.TEXT),
                    Property(name="simhash64", data_type=DataType.TEXT),
                ],
            )

        if not self.client.collections.exists(self.TABLE_COLLECTION):
            self.client.collections.create(
                name=self.TABLE_COLLECTION,
                vector_config=Configure.Vectors.self_provided(),
                properties=[
                    Property(name="doc_id", data_type=DataType.TEXT),
                    Property(name="doc_version", data_type=DataType.INT),
                    Property(name="table_id", data_type=DataType.TEXT),
                    Property(name="table_index", data_type=DataType.INT),
                    Property(name="section_path", data_type=DataType.TEXT),
                    Property(name="section_title", data_type=DataType.TEXT),
                    Property(name="page_start", data_type=DataType.INT),
                    Property(name="page_end", data_type=DataType.INT),
                    Property(name="table_title", data_type=DataType.TEXT),
                    Property(name="headers", data_type=DataType.TEXT_ARRAY),
                    Property(name="schema_signature", data_type=DataType.TEXT),
                    Property(name="table_markdown", data_type=DataType.TEXT),
                    Property(name="table_text_projection", data_type=DataType.TEXT),
                    Property(name="row_count", data_type=DataType.INT),
                    Property(name="col_count", data_type=DataType.INT),
                    Property(name="rows_json", data_type=DataType.OBJECT_ARRAY),
                ],
            )

    def upsert_chunks(self, chunks: List[ChunkRecord]) -> None:
        coll = self.client.collections.get(self.CHUNK_COLLECTION)
        with coll.batch.dynamic() as batch:
            for chunk in chunks:
                batch.add_object(
                    properties={
                        "doc_id": chunk.doc_id,
                        "doc_version": chunk.doc_version,
                        "chunk_id": chunk.chunk_id,
                        "chunk_index": chunk.chunk_index,
                        "section_path": chunk.section_path,
                        "section_title": chunk.section_title,
                        "text_raw": chunk.text_raw,
                        "text_canonical": chunk.text_canonical,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                        "is_auxiliary": chunk.is_auxiliary,
                        "sha256": chunk.sha256,
                        "simhash64": chunk.simhash64,
                    },
                    vector=chunk.vector,
                )

    def upsert_tables(self, tables: List[TableRecord]) -> None:
        coll = self.client.collections.get(self.TABLE_COLLECTION)
        with coll.batch.dynamic() as batch:
            for table in tables:
                batch.add_object(
                    properties={
                        "doc_id": table.doc_id,
                        "doc_version": table.doc_version,
                        "table_id": table.table_id,
                        "table_index": table.table_index,
                        "section_path": table.section_path,
                        "section_title": table.section_title,
                        "page_start": table.page_start,
                        "page_end": table.page_end,
                        "table_title": table.table_title,
                        "headers": table.headers,
                        "schema_signature": table.schema_signature,
                        "table_markdown": table.table_markdown,
                        "table_text_projection": table.table_text_projection,
                        "row_count": table.row_count,
                        "col_count": table.col_count,
                        "rows_json": table.rows_json,
                    },
                    vector=table.vector,
                )

    def fetch_doc_chunks(self, doc_id: str) -> List[dict[str, Any]]:
        coll = self.client.collections.get(self.CHUNK_COLLECTION)
        response = coll.query.fetch_objects(
            filters=Filter.by_property("doc_id").equal(doc_id),
            limit=10000,
            return_vector=True,
        )
        rows: List[dict[str, Any]] = []
        for obj in response.objects:
            rows.append({**obj.properties, "_vector": obj.vector})
        return rows

    def fetch_doc_tables(self, doc_id: str) -> List[dict[str, Any]]:
        coll = self.client.collections.get(self.TABLE_COLLECTION)
        response = coll.query.fetch_objects(
            filters=Filter.by_property("doc_id").equal(doc_id),
            limit=5000,
            return_vector=True,
        )
        rows: List[dict[str, Any]] = []
        for obj in response.objects:
            rows.append({**obj.properties, "_vector": obj.vector})
        return rows

```
