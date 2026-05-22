"""
Core domain entities — pure Python dataclasses and enums with no infrastructure dependency.
These are the system-of-record types. All layers must map to/from these.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class IngestionStatus(str, Enum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PLANNING = "planning"
    EXTRACTING = "extracting"
    CANONICALIZING = "canonicalizing"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class ComparisonStatus(str, Enum):
    QUEUED = "queued"
    ALIGNING = "aligning"
    DIFFING = "diffing"
    BUILDING_CHANGE_RECORDS = "building_change_records"
    SCORING = "scoring"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class NodeType(str, Enum):
    PARAGRAPH = "paragraph"
    CLAUSE = "clause"
    TABLE = "table"
    FORMULA = "formula"
    FIGURE = "figure"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    FOOTNOTE = "footnote"
    DEFINITION = "definition"


class SuppressionReason(str, Enum):
    TOC = "toc"
    HEADER_FOOTER = "header_footer"
    BOILERPLATE = "boilerplate"
    PAGE_NUMBER = "page_number"
    OCR_FRAGMENT = "ocr_fragment"
    LOW_CONFIDENCE = "low_confidence"


class ChangeType(str, Enum):
    """Canonical change type enum. Matches Literal["ADDED","REMOVED","MODIFIED"] used
    in change_records.py — both coexist; future consolidation will remove the Literal."""
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"
    MOVED = "MOVED"
    SPLIT = "SPLIT"
    MERGED = "MERGED"


class ChangeSeverity(str, Enum):
    """Severity levels produced by SeverityClassifier. Exactly three values."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------


@dataclass
class DocumentDomain:
    id: str
    name: str
    version: str
    upload_date: datetime
    size_bytes: int
    category: str
    file_path: str
