from __future__ import annotations

from dataclasses import replace

from grc_policy_server.services.comparision.policy_semantics import (
    clean_policy_text,
    ends_with_terminal_punctuation,
    is_noise_text,
    starts_with_lowercase,
)
from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk


def preprocess_parsed_chunks(parsed_chunks: list[ParsedChunk]) -> list[ParsedChunk]:
    processed: list[ParsedChunk] = []

    for chunk in parsed_chunks:
        clean_text = clean_policy_text(chunk.text)
        if chunk.chunk_type in {"clause", "table"} and (
            not clean_text or is_noise_text(clean_text)
        ):
            continue

        metadata = dict(chunk.metadata)
        metadata["clean_text"] = clean_text
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
