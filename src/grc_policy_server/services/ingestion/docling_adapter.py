from __future__ import annotations

import logging
from io import BytesIO
from typing import Optional

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    OcrAutoOptions,
    PdfPipelineOptions,
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
        pdf_options.images_scale = 0.5
        # pdf_options.do_formula_enrichment = True

        # pdf_options.generate_table_images = False
        # pdf_options.generate_page_images = True
        pdf_options.accelerator_options.device = settings.docling_accelerator_device
        pdf_options.accelerator_options.num_threads = (
            settings.docling_accelerator_threads
        )
        pdf_options.accelerator_options.cuda_use_flash_attention2 = (
            settings.docling_cuda_use_flash_attention2
        )
        pdf_options.ocr_options = OcrAutoOptions(force_full_page_ocr=False)

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
        result = converter.convert(source)

        if not result.document:
            raise RuntimeError(f"Docling conversion failed for {filename}")

        return result.document
