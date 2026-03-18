from __future__ import annotations

import re
from dataclasses import replace

from grc_policy_server.services.comparision.policy_semantics import (
    clean_policy_text,
    ends_with_terminal_punctuation,
    is_noise_text,
    starts_with_lowercase,
)
from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk
from grc_policy_server.utils.hashing import normalize_for_comparison

_AUXILIARY_TITLE_RE = re.compile(
    r"^\s*(table of contents|contents|index|glossary|list of figures|list of tables)\s*$",
    re.IGNORECASE,
)


def looks_like_auxiliary(text: str, section_title: str) -> bool:
    body = (text or "").strip()
    if not body:
        return True

    if section_title and _AUXILIARY_TITLE_RE.match(section_title.strip()):
        return True

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    dot_leaders = len(re.findall(r"\.{3,}", body))
    trailing_numbers = sum(1 for line in lines if re.search(r"\s\d{1,4}\s*$", line))
    short_lines = sum(1 for line in lines if len(line) <= 70)

    return (
        len(lines) >= 6
        and dot_leaders >= 3
        and trailing_numbers >= 3
        and short_lines / max(len(lines), 1) > 0.6
    )


def preprocess_parsed_chunks(parsed_chunks: list[ParsedChunk]) -> list[ParsedChunk]:
    processed: list[ParsedChunk] = []

    for chunk in parsed_chunks:
        section_title = chunk.section_path[-1] if chunk.section_path else (chunk.title or "")
        if chunk.chunk_type in {"clause", "table"} and looks_like_auxiliary(
            chunk.text, section_title
        ):
            continue

        if chunk.chunk_type == "table":
            clean_source = str(chunk.metadata.get("table_clean_text") or chunk.text)
        else:
            clean_source = chunk.text
        clean_text = clean_policy_text(clean_source)
        if chunk.chunk_type in {"clause", "table"} and (
            not clean_text or is_noise_text(clean_text)
        ):
            continue

        metadata = dict(chunk.metadata)
        metadata["clean_text"] = clean_text
        metadata["canonical_text"] = normalize_for_comparison(clean_source)
        if chunk.chunk_type == "table" and chunk.markdown_text:
            metadata["table_markdown"] = chunk.markdown_text
        current = replace(chunk, metadata=metadata)

        if _should_merge(processed[-1], current) if processed else False:
            processed[-1] = _merge_chunks(processed[-1], current)
            continue

        processed.append(current)

    return processed


def _should_merge(previous: ParsedChunk, current: ParsedChunk) -> bool:
    if previous.chunk_type != "clause" or current.chunk_type != "clause":
        return False
    if previous.section_path != current.section_path:
        return False
    if previous.page_number != current.page_number:
        return False
    previous_text = previous.metadata.get("clean_text") or ""
    current_text = current.metadata.get("clean_text") or ""
    if not previous_text or not current_text:
        return False
    return (
        not ends_with_terminal_punctuation(previous.text)
        and starts_with_lowercase(current.text)
    )


def _merge_chunks(previous: ParsedChunk, current: ParsedChunk) -> ParsedChunk:
    merged_text = f"{previous.text.rstrip()} {current.text.lstrip()}".strip()
    # Merge markdown_text if both exist, otherwise use whichever is available
    merged_markdown: str | None = None
    if previous.markdown_text and current.markdown_text:
        merged_markdown = f"{previous.markdown_text.rstrip()}\n{current.markdown_text.lstrip()}".strip()
    elif previous.markdown_text:
        merged_markdown = previous.markdown_text
    elif current.markdown_text:
        merged_markdown = current.markdown_text

    merged_metadata = dict(previous.metadata)
    merged_metadata["clean_text"] = clean_policy_text(merged_text)
    merged_metadata["captions"] = _merge_unique(
        previous.metadata.get("captions"),
        current.metadata.get("captions"),
    )
    merged_metadata["doc_items_refs"] = _merge_unique(
        previous.metadata.get("doc_items_refs"),
        current.metadata.get("doc_items_refs"),
    )

    return ParsedChunk(
        chunk_type=previous.chunk_type,
        text=merged_text,
        section_path=previous.section_path,
        page_number=previous.page_number,
        ordinal=previous.ordinal,
        title=previous.title,
        markdown_text=merged_markdown,
        docling_path=previous.docling_path or current.docling_path,
        source_refs=tuple(_merge_unique(previous.source_refs, current.source_refs)),
        labels=tuple(_merge_unique(previous.labels, current.labels)),
        metadata=merged_metadata,
        source=previous.source,
    )


def _merge_unique(left, right) -> list[str]:
    merged: list[str] = []
    for values in (left or [], right or []):
        for value in values:
            string_value = str(value)
            if string_value and string_value not in merged:
                merged.append(string_value)
    return merged
