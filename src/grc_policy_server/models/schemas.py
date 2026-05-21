from typing import List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


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
    nodeId: Optional[str] = None
    textHash: Optional[str] = None
    bbox: Optional[dict] = None


class ChangeDetail(BaseModel):
    """Specific change detail for UI highlighting."""

    type: Literal["added", "removed", "modified"]
    text: str
    oldValue: str | None = None  # For modified items
    newValue: str | None = None  # For modified items
    location: str | None = None  # e.g., "Row 5" for tables, "Line 3" for text


class KeyDifference(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    changeType: Literal["ADDED", "REMOVED", "MODIFIED"]
    section: str
    doc1Content: str | None
    doc2Content: str | None
    impact: str
    changeSeverity: Literal["low", "medium", "high"] = Field(
        default="medium",
        validation_alias=AliasChoices("changeSeverity", "severity"),
    )
    doc1Reference: DocumentReference | None
    doc2Reference: DocumentReference | None
    nodeType: str = "clause"  # "clause" or "table"
    changes: List[ChangeDetail] = Field(
        default_factory=list
    )  # Specific changes for highlighting
    markdownDiffSummary: Optional[str] = None  # LLM-generated markdown diff summary
    requiresHumanReview: bool = False
    severityConfidence: Optional[float] = None


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
    comparisonMode: Literal["auditor_grade", "simple"] = "auditor_grade"
    requireHumanReview: bool = False
    hiddenDiffsCount: int = 0


class CompareRequest(BaseModel):
    doc1: Document
    doc2: Document
    forceReExtract: bool = False
    auditMode: bool = True
    saveToDb: bool = False


TestingDepartment = Literal["EMC", "Safety", "Environment"]


class CompareStreamV4Request(BaseModel):
    """Stream-oriented compare request with minimal payload."""

    doc1Id: str
    doc2Id: str
    testingDepartment: TestingDepartment
    forceReExtract: bool = False


class DiffChunk(BaseModel):
    type: str
    content: str


class CompareResponse(BaseModel):
    diffs: List[DiffChunk]


class CompareV2JobCreateResponse(BaseModel):
    jobId: str
    status: Literal["queued", "finished"]
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
    nodeType: str | None = None
    canonicalText: str | None = None
    markdown: str | None = None
    tableMarkdown: str | None = None
    chunkIndex: int | None = None
    score: float | None = None
    distance: float | None = None
    scores: dict[str, float | None] | None = None


class HybridSearchDocumentResult(BaseModel):
    documentId: str
    chunks: List[HybridSearchChunk]


class HybridSearchResponse(BaseModel):
    query: str
    results: List[HybridSearchDocumentResult]


# ---------------------------------------------------------------------------
# Storage provider configs + remote ingestion
# ---------------------------------------------------------------------------


StorageProviderType = Literal["s3", "azure_blob", "gdrive"]


class StorageProviderConfigCreateRequest(BaseModel):
    providerType: StorageProviderType
    name: str
    config: dict = Field(default_factory=dict)
    secrets: dict = Field(default_factory=dict)


class StorageProviderConfigUpdateRequest(BaseModel):
    name: str | None = None
    config: dict | None = None
    secrets: dict | None = None


class StorageProviderConfig(BaseModel):
    providerId: str
    providerType: StorageProviderType
    name: str
    config: dict = Field(default_factory=dict)
    createdAt: str
    updatedAt: str


class StorageProviderListResponse(BaseModel):
    providers: List[StorageProviderConfig]


class IngestSource(BaseModel):
    uri: str
    filename: str | None = None
    providerId: str | None = None


class IngestSourcesRequest(BaseModel):
    sources: List[IngestSource]
