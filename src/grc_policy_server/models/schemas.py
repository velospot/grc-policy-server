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
    impact: str
    doc1Reference: DocumentReference | None
    doc2Reference: DocumentReference | None


class ActionItem(BaseModel):
    priority: str
    action: str
    timeline: str
    owner: str


class ComparisonResult(BaseModel):
    summary: str
    keyDifferences: List[KeyDifference]
    actionPlan: List[ActionItem]
    followUpQuestions: List[str]


class CompareRequest(BaseModel):
    doc1: Document
    doc2: Document


class DiffChunk(BaseModel):
    type: str
    content: str


class CompareResponse(BaseModel):
    diffs: List[DiffChunk]


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
