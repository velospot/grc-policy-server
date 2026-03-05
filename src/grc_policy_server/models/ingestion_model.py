from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class IngestOptions(BaseModel):
    # Docling pipeline toggles
    auto_ocr: bool = True
    force_full_page_ocr: bool = False  # if True, OCR every page (slower)
    do_table_structure: bool = True

    # Chunking
    merge_list_items: bool = True

    # Storage toggles
    save_to_neo4j: bool = False
    save_to_weaviate: bool = False


class IngestRequest(BaseModel):
    # For URL ingestion (uploads handled via multipart)
    urls: list[HttpUrl] = Field(default_factory=list)
    options: IngestOptions = Field(default_factory=IngestOptions)


class ChunkMeta(BaseModel):
    document_id: str
    document_title: str | None = None
    source_type: Literal["upload", "url"]
    source_name: str | None = None  # filename or URL

    page_number: int | None = None
    section_path: list[str] = Field(default_factory=list)  # [heading, subheading, ...]
    docling_path: str | None = None  # e.g. "#/main-text/1" etc.


class TableInfo(BaseModel):
    caption: str | None = None
    markdown: str | None = None
    doc_items_refs: list[str] = Field(default_factory=list)


class ImageInfo(BaseModel):
    caption: str | None = None
    description: str | None = None
    doc_items_refs: list[str] = Field(default_factory=list)


class DocumentChunk(BaseModel):
    chunk_id: str
    main_text: str
    tableInfo: TableInfo | None = None
    imageInfo: ImageInfo | None = None
    metadata: ChunkMeta

    # Optional vector (if you compute embeddings in-service)
    vector: list[float] | None = None


class DocumentResult(BaseModel):
    document_id: str
    docling_json: dict[str, Any]
    chunks: list[DocumentChunk]


class IngestResponse(BaseModel):
    results: list[DocumentResult]
