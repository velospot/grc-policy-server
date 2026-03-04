from __future__ import annotations

from collections import defaultdict
from importlib import import_module
from io import BytesIO
import logging
import re
import shutil
from typing import Any, Iterable

from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk

logger = logging.getLogger(__name__)

_OCR_SECTION = ("OCR Recovered Text",)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def build_ocr_fallback_chunks(
    *,
    filename: str,
    content: bytes,
    parsed_chunks: Iterable[ParsedChunk],
    page_count: int,
    min_chars_per_page: int,
    min_total_chars: int,
    render_dpi: int,
    languages: str,
    page_segmentation_mode: int,
) -> tuple[list[ParsedChunk], dict[str, Any], set[int]]:
    if not filename.lower().endswith(".pdf"):
        return [], {"enabled": False, "used": False, "reason": "non_pdf"}, set()
    if page_count <= 0:
        return [], {"enabled": False, "used": False, "reason": "no_pages"}, set()

    chunk_list = list(parsed_chunks)
    text_by_page = _page_text_lengths(chunk_list)
    total_text = sum(text_by_page.values())
    sparse_pages = {
        page for page in range(1, page_count + 1) if text_by_page.get(page, 0) < min_chars_per_page
    }
    needs_fallback = total_text < min_total_chars or bool(sparse_pages)
    if not needs_fallback:
        return (
            [],
            {
                "enabled": True,
                "used": False,
                "reason": "sufficient_docling_text",
                "page_lengths": dict(text_by_page),
            },
            set(),
        )

    try:
        pytesseract = import_module("pytesseract")
    except ImportError:
        logger.warning("pytesseract not installed; OCR fallback disabled")
        return (
            [],
            {
                "enabled": True,
                "used": False,
                "reason": "missing_pytesseract",
                "page_lengths": dict(text_by_page),
            },
            set(),
        )
    if shutil.which("tesseract") is None:
        logger.info("tesseract binary not installed; OCR fallback disabled")
        return (
            [],
            {
                "enabled": True,
                "used": False,
                "reason": "missing_tesseract_binary",
                "page_lengths": dict(text_by_page),
            },
            set(),
        )

    try:
        import pypdfium2
    except ImportError:
        logger.warning("pypdfium2 not installed; OCR fallback disabled")
        return (
            [],
            {
                "enabled": True,
                "used": False,
                "reason": "missing_pypdfium2",
                "page_lengths": dict(text_by_page),
            },
            set(),
        )

    section_hints = _page_section_hints(chunk_list, page_count)
    ocr_chunks: list[ParsedChunk] = []
    ocr_pages_used: set[int] = set()
    next_ordinal = (max((chunk.ordinal for chunk in chunk_list), default=-1) + 1)

    try:
        with pypdfium2.PdfDocument(BytesIO(content)) as pdf:
            for page_number in sorted(sparse_pages):
                page = pdf[page_number - 1]
                pil_image = page.render(scale=render_dpi / 72.0).to_pil()
                text = pytesseract.image_to_string(
                    pil_image,
                    lang=languages,
                    config=f"--psm {page_segmentation_mode}",
                )
                normalized = _normalize_ocr_text(text)
                if len(normalized) < min_chars_per_page:
                    continue

                ocr_pages_used.add(page_number)
                section_path = section_hints.get(page_number) or _OCR_SECTION
                for block_index, block in enumerate(_split_ocr_blocks(normalized)):
                    ocr_chunks.append(
                        ParsedChunk(
                            chunk_type="clause",
                            text=block,
                            section_path=section_path,
                            page_number=page_number,
                            ordinal=next_ordinal,
                            title=None,
                            docling_path=None,
                            source_refs=(),
                            labels=("OCR",),
                            metadata={
                                "ocr": True,
                                "ocr_block_index": block_index,
                                "ocr_page_number": page_number,
                                "ocr_engine": "pytesseract",
                            },
                            source="pytesseract",
                        )
                    )
                    next_ordinal += 1
    except Exception:
        logger.exception("failed to run OCR fallback")
        return (
            [],
            {
                "enabled": True,
                "used": False,
                "reason": "ocr_exception",
                "page_lengths": dict(text_by_page),
            },
            set(),
        )

    return (
        ocr_chunks,
        {
            "enabled": True,
            "used": bool(ocr_chunks),
            "page_lengths": dict(text_by_page),
            "pages_considered": sorted(sparse_pages),
            "pages_ocr_used": sorted(ocr_pages_used),
            "provider": "pytesseract",
        },
        ocr_pages_used,
    )


def _page_text_lengths(parsed_chunks: Iterable[ParsedChunk]) -> dict[int, int]:
    lengths: dict[int, int] = defaultdict(int)
    for chunk in parsed_chunks:
        if chunk.page_number is None:
            continue
        if chunk.chunk_type == "heading":
            continue
        lengths[chunk.page_number] += len((chunk.text or "").strip())
    return dict(lengths)


def _page_section_hints(
    parsed_chunks: list[ParsedChunk],
    page_count: int,
) -> dict[int, tuple[str, ...]]:
    by_page: dict[int, tuple[str, ...]] = {}
    for chunk in sorted(parsed_chunks, key=lambda item: (item.page_number or 0, item.ordinal)):
        if chunk.page_number is None or not chunk.section_path:
            continue
        by_page.setdefault(chunk.page_number, chunk.section_path)

    hints: dict[int, tuple[str, ...]] = {}
    last_seen: tuple[str, ...] = ()
    for page in range(1, page_count + 1):
        if page in by_page:
            last_seen = by_page[page]
        hints[page] = last_seen

    next_seen: tuple[str, ...] = ()
    for page in range(page_count, 0, -1):
        if page in by_page:
            next_seen = by_page[page]
        if not hints[page] and next_seen:
            hints[page] = next_seen

    return hints


def _normalize_ocr_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r", "").splitlines()]
    compact_lines = [line for line in lines if line.strip()]
    return "\n".join(compact_lines).strip()


def _split_ocr_blocks(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    if not paragraphs:
        paragraphs = [text.strip()] if text.strip() else []

    blocks: list[str] = []
    for paragraph in paragraphs:
        collapsed = " ".join(paragraph.split())
        if len(collapsed) <= 1200:
            blocks.append(collapsed)
            continue

        current = ""
        for sentence in _SENTENCE_SPLIT_RE.split(collapsed):
            sentence = sentence.strip()
            if not sentence:
                continue
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > 900:
                blocks.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            blocks.append(current)

    return blocks or [text]
