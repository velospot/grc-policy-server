"""Canonical output type for document extraction adapters.

Both DoclingAdapter and OpenDataLoaderAdapter convert raw bytes into
ParsedChunk lists via their respective chunkers.  ExtractorOutput wraps
that result with provenance metadata so DocumentIngestionService can
treat all extractors uniformly.

Adding a new extractor (e.g. Azure Document Intelligence):
  1. Produce a list[ParsedChunk] from raw bytes.
  2. Wrap it in ExtractorOutput with the extractor_name you choose.
  3. Pass to ChunkEnricher.enrich() and continue the ingestion pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from grc_policy_server.services.ingestion.hierarchy_models import ParsedChunk


@dataclass
class ExtractorOutput:
    """Result of a document extraction pass.

    Attributes:
        parsed_chunks: Structured content ready for ChunkEnricher and
            hierarchy building.
        extractor_name: Identifier of the adapter that produced this output
            (e.g. "docling", "opendataloader", "pytesseract").
        raw_export: Optional raw intermediate representation (Docling JSON
            dict or OPD element list) kept for audit / debug storage.
        ocr_used: True when OCR was applied to any page.
        text_density: Estimated ratio of native text to total content
            (0.0 = fully scanned, 1.0 = fully native text).
        page_count: Number of pages extracted, if known.
        metadata: Extractor-specific key/value pairs (e.g. Docling language
            detection result, OPD version, VLM model used).
    """

    parsed_chunks: list[ParsedChunk]
    extractor_name: str
    raw_export: dict[str, Any] | list[Any] | None = None
    ocr_used: bool = False
    text_density: float = 1.0
    page_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
