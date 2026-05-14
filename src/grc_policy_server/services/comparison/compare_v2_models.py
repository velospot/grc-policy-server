from __future__ import annotations

from pydantic import BaseModel

from grc_policy_server.models.schemas import Document


class CompareTaskPayload(BaseModel):
    doc1: Document
    doc2: Document
    force_re_extract: bool = False
    cache_key: str
    audit_mode: bool = True
    save_to_db: bool = False
