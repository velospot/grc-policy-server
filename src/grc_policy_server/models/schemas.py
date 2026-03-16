from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class Document(BaseModel):
    id: str
    name: str
    version: str
    uploadDate: str
    size: str
    category: str


class DocumentReference(BaseModel):
    section: str
    page: int
    lineStart: Optional[int] = None
    lineEnd: Optional[int] = None
    sourceText: str


class KeyDifference(BaseModel):
    changeType: Literal["ADDED", "REMOVED", "MODIFIED"]
    section: str
    doc1Content: str | None
    doc2Content: str | None
    impact: Literal["Critical", "High", "Medium", "Low"]
    doc1Reference: DocumentReference | None
    doc2Reference: DocumentReference | None


class ActionItem(BaseModel):
    priority: str
    action: str
    timeline: str
    owner: str


class SectionAccuracyMetrics(BaseModel):
    section: str
    avg_match_distance: float
    avg_match_score: float
    match_count: int
    confidence: float


class ComparisonAccuracyMetrics(BaseModel):
    avg_match_distance: float
    avg_match_score: Optional[float] = None
    high_confidence_matches: int
    medium_confidence_matches: int
    low_confidence_matches: int
    total_matches: int
    overall_confidence: float
    confidence_breakdown: dict[str, int]
    section_metrics: List[SectionAccuracyMetrics]     


class ComparisonResult(BaseModel):
    summary: str
    keyDifferences: List[KeyDifference]
    actionPlan: List[ActionItem]
    followUpQuestions: List[str]
    accuracyMetrics: Optional[ComparisonAccuracyMetrics] = None


class CompareRequest(BaseModel):
    doc1: Document
    doc2: Document
    forceReExtract: bool = False


class CompareV2JobCreateResponse(BaseModel):
    jobId: str
    status: Literal["queued", "finished"] = "queued"
    cacheHit: bool = False
    result: ComparisonResult | None = None


class CompareV2JobStatusResponse(BaseModel):
    jobId: str
    status: Literal["queued", "running", "finished", "failed"]
    done: bool
    result: ComparisonResult | None = None
    error: str | None = None
    cacheHit: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok"]


class UploadDocumentResponse(BaseModel):
    filename: str
    contentType: str | None = None
    accepted: bool
    documentId: str | None = None
    chunksStored: int | None = None
    error: str | None = None


class UploadDocumentsResponse(BaseModel):
    acceptedCount: int
    rejectedCount: int
    results: List[UploadDocumentResponse]


class UploadV2JobCreateResponse(BaseModel):
    jobId: str
    status: Literal["queued"] = "queued"


class UploadV2JobStatusResponse(BaseModel):
    jobId: str
    status: Literal["queued", "running", "finished", "failed"]
    done: bool
    result: UploadDocumentsResponse | None = None
    error: str | None = None


class DeleteDocumentsRequest(BaseModel):
    documentIds: List[str]


class DeleteDocumentResult(BaseModel):
    documentId: str
    deleted: bool
    deletedChunks: int | None = None
    error: str | None = None


class DeleteDocumentsResponse(BaseModel):
    deletedCount: int
    failedCount: int
    results: List[DeleteDocumentResult]


class HybridSearchRequest(BaseModel):
    documentId1: str
    documentId2: str
    query: str
    limit: int = Field(default=3, ge=1, le=50)


class HybridSearchChunk(BaseModel):
    chunkId: str
    documentId: str
    sectionPath: str
    text: str
    chunkIndex: int | None = None
    score: float | None = None


class HybridSearchDocumentResult(BaseModel):
    documentId: str
    chunks: List[HybridSearchChunk]


class HybridSearchResponse(BaseModel):
    query: str
    results: List[HybridSearchDocumentResult]
