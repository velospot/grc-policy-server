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
    tableData: Optional[dict] = None
    # Shape: {caption, headers: [[str]], rows: [[str]], num_rows, num_cols, source_extractor}
    formulaLatex: Optional[str] = None


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
    complianceExplanation: Optional[str] = None  # deterministic compliance-semantic narrative
    # Extraction confidence of the underlying evidence (min of both sides);
    # None when source nodes carry no confidence data.
    extractionConfidence: Optional[float] = None
    # Why this diff needs human review: extraction-driven codes
    # ("low_ocr_confidence", "low_confidence_table", "unparsed_formula", ...)
    # and/or semantic codes emitted by the severity classifier.
    reviewReasons: List[str] = Field(default_factory=list)


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
    language_breakdown: Optional[dict[str, int]] = None


class ComparisonResult(BaseModel):
    summary: str
    keyDifferences: List[KeyDifference]
    actionPlan: List[ActionItem]
    followUpQuestions: List[str]
    accuracyMetrics: Optional[ComparisonAccuracyMetrics] = None
    comparisonMode: Literal["auditor_grade", "simple"] = "auditor_grade"
    requireHumanReview: bool = False
    hiddenDiffsCount: int = 0
    warnings: List[str] = Field(default_factory=list)
    suppressedDiffsCount: int = 0  # LOW-severity diffs excluded from keyDifferences
    skippedSections: List[str] = Field(default_factory=list)  # sections with no semantic change


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


class CompareStreamV5Request(BaseModel):
    """ID-based stream request for v5 hybrid comparison endpoints."""

    doc1Id: str
    doc2Id: str
    testingDepartment: TestingDepartment
    forceReExtract: bool = False


class GraphCompareTaskPayload(BaseModel):
    """Celery task payload for graph-tree comparison jobs."""

    doc1: Document
    doc2: Document
    testing_department: str = ""
    include_unchanged: bool = False
    audit_mode: bool = True
    force_re_extract: bool = False
    cache_key: str


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
    warnings: list[str] | None = None


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


# ---------------------------------------------------------------------------
# Graph-tree comparison API
# ---------------------------------------------------------------------------


class GraphCompareRequest(BaseModel):
    doc1: Document
    doc2: Document
    testingDepartment: TestingDepartment | None = None
    includeUnchanged: bool = False
    auditMode: bool = True
    forceReExtract: bool = False


class GraphNodeRef(BaseModel):
    nodeId: str
    stableId: str
    layer: str
    label: str
    ontologyType: str | None = None
    title: str = ""
    sectionPath: str = ""
    page: int | None = None
    sourceNodeId: str | None = None
    properties: dict = Field(default_factory=dict)


class GraphPropertyChange(BaseModel):
    path: str
    oldValue: object | None = None
    newValue: object | None = None


class GraphRelationshipChange(BaseModel):
    changeType: Literal["ADDED", "REMOVED"]
    relType: str
    targetKey: str
    targetLabel: str = ""


class GraphChangeRecord(BaseModel):
    changeId: str
    changeType: Literal["ADDED", "REMOVED", "MODIFIED", "UNCHANGED"]
    severity: Literal["low", "medium", "high"]
    layer: str
    ontologyType: str | None = None
    title: str = ""
    sectionPath: str = ""
    doc1Node: GraphNodeRef | None = None
    doc2Node: GraphNodeRef | None = None
    propertyChanges: List[GraphPropertyChange] = Field(default_factory=list)
    relationshipChanges: List[GraphRelationshipChange] = Field(default_factory=list)
    confidence: float = 1.0
    requiresHumanReview: bool = False
    rationale: str = ""


class GraphComparisonSummary(BaseModel):
    totalChanges: int
    added: int
    removed: int
    modified: int
    unchanged: int = 0
    highSeverity: int
    mediumSeverity: int
    lowSeverity: int
    requiresHumanReview: bool


class GraphComparisonResult(BaseModel):
    comparisonId: str
    doc1Id: str
    doc2Id: str
    comparisonMode: Literal["document_graph_tree"] = "document_graph_tree"
    summary: GraphComparisonSummary
    changes: List[GraphChangeRecord]
    warnings: List[str] = Field(default_factory=list)


class ExtractionFlag(BaseModel):
    """Alert for human review during extraction."""
    flag_type: str  # "low_ocr_confidence", "ambiguous_section", "stitching_ambiguity", etc.
    severity: Literal["warning", "error"]
    description: str
    affected_object: Optional[str] = None  # table_id, row_id, section_path, etc.
    confidence_if_applicable: Optional[float] = None


class CellConfidence(BaseModel):
    """Per-cell extraction confidence."""
    chunk_id: str
    column_name: Optional[str] = None
    column_role: Optional[str] = None  # "limit", "result", "measured", "condition", etc.
    confidence: float  # 0.0–1.0
    confidence_factors: dict[str, float] = Field(
        default_factory=lambda: {
            "cell_fill": 0.0,
            "cell_type_match": 0.0,
            "unit_validity": 0.0,
            "ocr_confidence": 0.0,
        }
    )
    flags: Optional[List[str]] = None  # ["missing_unit", "unparseable_number", etc.]


class RequirementConfidence(BaseModel):
    """Per-row/requirement extraction confidence."""
    row_id: str
    row_key: Optional[str] = None
    confidence: float  # 0.0–1.0 (min of key cell confidences)
    key_cell_confidences: dict[str, float] = Field(default_factory=dict)
    applicability_confidence: float = 1.0  # confidence in footnote/condition scope
    requires_review: bool = False
    review_reason: Optional[str] = None


class ConfidenceMetrics(BaseModel):
    """Document-level confidence summary for ingestion."""
    document_id: str
    table_confidence_avg: float = 0.0
    cell_confidence_avg: float = 0.0
    requirement_confidence_avg: float = 0.0
    extraction_flags: List[ExtractionFlag] = Field(default_factory=list)
    high_confidence_cells: int = 0  # confidence >= 0.85
    medium_confidence_cells: int = 0  # 0.50–0.85
    low_confidence_cells: int = 0  # < 0.50
    requires_human_review_count: int = 0
    # Docling-native ConfidenceReport (document-level scores + grades, no pages):
    # parse/layout/table/ocr_score, mean/low_score, mean/low_grade.
    docling_confidence: Optional[dict] = None
