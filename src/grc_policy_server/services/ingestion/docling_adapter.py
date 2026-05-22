from __future__ import annotations

import logging
import time
from io import BytesIO
from typing import Any, Optional

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    OcrAutoOptions,
    PdfPipelineOptions,
    TableStructureOptions,
    TesseractCliOcrOptions,
)
from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    WordFormatOption,
)
from docling_core.types.io import DocumentStream

from grc_policy_server.core.config import settings

logger = logging.getLogger(__name__)


def _build_vlm_converter() -> DocumentConverter:
    """Build a Docling converter backed by granite-docling VLM via Ollama API.

    Uses VlmPipeline which sends each page image to the granite-docling model
    for layout + table extraction, improving accuracy on dense/complex tables.
    """
    from docling.datamodel.pipeline_options import VlmPipelineOptions, VlmConvertOptions
    from docling.datamodel.vlm_engine_options import ApiVlmEngineOptions, VlmEngineType
    from docling.pipeline.vlm_pipeline import VlmPipeline

    base_url = settings.docling_vlm_ollama_url.rstrip("/")
    vlm_options = VlmConvertOptions.from_preset(
        "granite_docling",
        engine_options=ApiVlmEngineOptions(
            engine_type=VlmEngineType.API_OLLAMA,
            url=f"{base_url}/v1/chat/completions",
            timeout=settings.docling_vlm_timeout_sec,
            params={"model": settings.docling_vlm_model},
        ),
    )
    pipeline_options = VlmPipelineOptions(
        vlm_options=vlm_options,
        enable_remote_services=True,
        generate_page_images=True,
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=VlmPipeline,
                pipeline_options=pipeline_options,
            ),
        }
    )


class DoclingAdapter:
    def __init__(self) -> None:
        self._converter: Optional[DocumentConverter] = None

    def _build_converter(
        self,
        *,
        auto_ocr: bool,
        force_full_page_ocr: bool,
        do_table_structure: bool,
    ) -> DocumentConverter:
        pdf_options = PdfPipelineOptions()
        pdf_options.do_ocr = auto_ocr
        pdf_options.do_table_structure = do_table_structure
        pdf_options.table_structure_options = TableStructureOptions(
            do_cell_matching=False
        )
        pdf_options.images_scale = 2
        try:
            pdf_options.do_formula_enrichment = True
        except AttributeError:
            pass  # older docling version without formula enrichment support

        # pdf_options.generate_table_images = False
        # pdf_options.generate_page_images = True
        pdf_options.accelerator_options.device = settings.docling_accelerator_device
        pdf_options.accelerator_options.num_threads = (
            settings.docling_accelerator_threads
        )
        pdf_options.accelerator_options.cuda_use_flash_attention2 = (
            settings.docling_cuda_use_flash_attention2
        )
        pdf_options.ocr_options = OcrAutoOptions(force_full_page_ocr=True)
        pdf_options.layout_batch_size = 64
        pdf_options.table_batch_size = 4
        if force_full_page_ocr:
            pdf_options.ocr_options = TesseractCliOcrOptions(
                force_full_page_ocr=True,
                lang=["eng", "deu", "fra", "spa"],
            )

        format_options = {
            InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
            InputFormat.DOCX: WordFormatOption(),
        }
        logger.info(
            "docling accelerator configured device=%s threads=%s",
            settings.docling_accelerator_device,
            settings.docling_accelerator_threads,
        )
        return DocumentConverter(format_options=format_options)

    def convert_bytes_vlm(
        self,
        *,
        filename: str,
        content: bytes,
    ):
        """Convert using the VLM pipeline (granite-docling via Ollama) for better table accuracy.

        Falls back to standard pipeline if VLM is not available.
        """
        try:
            converter = _build_vlm_converter()
            stream = BytesIO(content)
            source = DocumentStream(name=filename, stream=stream)
            start_time = time.time()
            result = converter.convert(source)
            pipeline_runtime = time.time() - start_time
            if result.document:
                num_pages = len(result.pages)
                logger.info(
                    "VLM conversion completed in %.2fs (%.2f pages/s)",
                    pipeline_runtime,
                    num_pages / pipeline_runtime if pipeline_runtime > 0 else 0,
                )
                doc = result.document
                continuation_hints = self._detect_multi_page_continuations(doc)
                doc._continuation_hints = continuation_hints
                return doc
        except Exception:
            logger.warning(
                "VLM conversion failed for %s; falling back to standard pipeline", filename,
                exc_info=True,
            )
        return self.convert_bytes(
            filename=filename,
            content=content,
            auto_ocr=True,
            force_full_page_ocr=False,
            do_table_structure=True,
        )

    def convert_bytes(
        self,
        *,
        filename: str,
        content: bytes,
        auto_ocr: bool,
        force_full_page_ocr: bool,
        do_table_structure: bool,
    ):
        converter = self._build_converter(
            auto_ocr=auto_ocr,
            force_full_page_ocr=force_full_page_ocr,
            do_table_structure=do_table_structure,
        )

        stream = BytesIO(content)
        source = DocumentStream(name=filename, stream=stream)
        start_time = time.time()
        result = converter.convert(source)
        pipeline_runtime = time.time() - start_time
        if not result.document:
            raise RuntimeError(f"Docling conversion failed for {filename}")

        num_pages = len(result.pages)
        logger.info(f"Document converted in {pipeline_runtime:.2f} seconds.")
        logger.info(f"  {num_pages / pipeline_runtime:.2f} pages/second.")
        doc = result.document
        continuation_hints = self._detect_multi_page_continuations(doc)
        if continuation_hints:
            logger.debug(
                "Docling detected %d multi-page table continuation pair(s): %s",
                len(continuation_hints),
                continuation_hints,
            )
        doc._continuation_hints = continuation_hints
        return doc

    def convert_bytes_page_range(
        self,
        *,
        filename: str,
        content: bytes,
        page_range: tuple[int, int],
        do_table_structure: bool = True,
    ) -> Any | None:
        """Re-run Docling on specific pages with force_full_page_ocr=True.

        page_range: (first_page, last_page) 1-indexed inclusive.
        Returns the Docling document or None on failure.
        """
        try:
            converter = self._build_converter(
                auto_ocr=True,
                force_full_page_ocr=True,
                do_table_structure=do_table_structure,
            )
            stream = BytesIO(content)
            source = DocumentStream(name=filename, stream=stream)
            result = converter.convert(source, page_range=page_range)
            if not result.document:
                return None
            return result.document
        except Exception:
            logger.exception(
                "convert_bytes_page_range failed filename=%s page_range=%s",
                filename,
                page_range,
            )
            return None

    @staticmethod
    def _table_cell_density(table: Any) -> float:
        """Return the fraction of non-empty cells in a Docling table."""
        try:
            grid = table.data.grid
            if not grid:
                return 0.0
            total = 0
            non_empty = 0
            for row in grid:
                for cell in row:
                    total += 1
                    if (getattr(cell, "text", None) or "").strip():
                        non_empty += 1
            return non_empty / total if total else 0.0
        except Exception:
            return 0.0

    def _detect_multi_page_continuations(self, doc: Any) -> list[tuple[int, int]]:
        """Scan consecutive-page table pairs for repeated header rows.

        When a table on page N+1 begins with a row whose cell texts match the
        headers of the last table on page N, the two are continuation candidates.

        Returns list of (table_index_page_n, table_index_page_n+1) pairs.
        Indices reference the flattened table item list from Docling's document.
        """
        try:
            tables = list(getattr(doc, "tables", None) or [])
        except Exception:
            return []

        if len(tables) < 2:
            return []

        def _page_of(table: Any) -> int:
            try:
                prov = table.prov
                if prov:
                    return int(prov[0].page_no)
            except Exception:
                pass
            return -1

        def _header_texts(table: Any) -> set[str]:
            """Return normalized header cell texts from the first row."""
            texts: set[str] = set()
            try:
                for cell in table.data.grid[0]:
                    t = (getattr(cell, "text", None) or "").strip().lower()
                    if t:
                        texts.add(t)
            except Exception:
                pass
            return texts

        hints: list[tuple[int, int]] = []
        for i in range(len(tables) - 1):
            t1, t2 = tables[i], tables[i + 1]
            p1, p2 = _page_of(t1), _page_of(t2)
            if p1 < 0 or p2 < 0 or p2 != p1 + 1:
                continue
            headers1 = _header_texts(t1)
            # Check whether the first row of t2 repeats headers from t1
            first_row2 = _header_texts(t2)
            if (
                headers1
                and first_row2
                and len(headers1 & first_row2) / len(headers1) >= 0.6
            ):
                hints.append((i, i + 1))
        return hints
