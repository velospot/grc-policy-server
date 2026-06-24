from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from grc_policy_server.models.schemas import Document


class CompareTaskPayload(BaseModel):
    doc1: Document
    doc2: Document
    force_re_extract: bool = False
    cache_key: str
    audit_mode: bool = True
    save_to_db: bool = False
    api_version: Literal["v2", "v5"] = "v2"
    testing_department: str | None = None
