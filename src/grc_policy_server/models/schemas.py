from typing import List, Optional

from pydantic import BaseModel


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
    section: str
    doc1Content: str
    doc2Content: str
    impact: str
    doc1Reference: DocumentReference
    doc2Reference: DocumentReference


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
