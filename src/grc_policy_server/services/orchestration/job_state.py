from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IngestionState(str, Enum):
    UPLOADED = "UPLOADED"
    QUALITY_CHECKED = "QUALITY_CHECKED"
    PARSED = "PARSED"
    EXTRACTED = "EXTRACTED"
    CANONICALIZED = "CANONICALIZED"
    TABLES_NORMALIZED = "TABLES_NORMALIZED"
    STANDARDS_RESOLVED = "STANDARDS_RESOLVED"
    ONTOLOGY_MAPPED = "ONTOLOGY_MAPPED"
    IDENTITIES_RESOLVED = "IDENTITIES_RESOLVED"
    GRAPH_BUILT = "GRAPH_BUILT"
    RELATIONSHIPS_BUILT = "RELATIONSHIPS_BUILT"
    EVIDENCE_VALIDATED = "EVIDENCE_VALIDATED"
    EMBEDDED = "EMBEDDED"
    READY_FOR_COMPARISON = "READY_FOR_COMPARISON"
    FAILED = "FAILED"


class ComparisonState(str, Enum):
    COMPARISON_REQUESTED = "COMPARISON_REQUESTED"
    ALIGNED = "ALIGNED"
    SPLIT_MERGE_ALIGNED = "SPLIT_MERGE_ALIGNED"
    DIFFED = "DIFFED"
    RULES_EVALUATED = "RULES_EVALUATED"
    REVIEW_TRIAGED = "REVIEW_TRIAGED"
    REPORTED = "REPORTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class JobRisk:
    extraction_risk: str = "LOW"
    ocr_used: bool = False
    review_required: bool = False
    review_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "extraction_risk": self.extraction_risk,
            "ocr_used": self.ocr_used,
            "review_required": self.review_required,
            "review_reason": self.review_reason,
        }


@dataclass
class JobState:
    job_id: str
    doc_id: str
    state: IngestionState = IngestionState.UPLOADED
    progress: int = 0
    artifacts: dict[str, Any] = field(default_factory=dict)
    risk: JobRisk = field(default_factory=JobRisk)
    resume_from: str | None = None
    failure_artifact_uri: str | None = None

    def can_resume(self) -> bool:
        return self.state == IngestionState.FAILED and self.resume_from is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "doc_id": self.doc_id,
            "state": self.state.value,
            "progress": self.progress,
            "artifacts": self.artifacts,
            "risk": self.risk.to_dict(),
            "resume_from": self.resume_from,
            "failure_artifact_uri": self.failure_artifact_uri,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

