from __future__ import annotations

import logging
from io import BytesIO
from typing import Optional

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import OcrOptions, PdfPipelineOptions
from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    WordFormatOption,
)

# Updated Import Paths
from docling_core.types.io import DocumentStream

logger = logging.getLogger(__name__)


class DoclingAdapter:
    def __init__(self) -> None:
        # Initialize as None if you want lazy loading,
        # but usually, you'd store the converter instance here.
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

        if force_full_page_ocr:
            # In latest versions, use OcrOptions for configuration
            pdf_options.ocr_options = OcrOptions(
                force_full_page_ocr=True, lang=["en", "de", "fr", "es"]
            )

        format_options = {
            InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
            InputFormat.DOCX: WordFormatOption(),
        }
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

        # converter.convert() handles a single DocumentStream or path directly
        result = converter.convert(source)

        if not result.document:
            raise RuntimeError(f"Docling conversion failed for {filename}")

        return result.document
