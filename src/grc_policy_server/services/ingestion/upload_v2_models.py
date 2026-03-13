from __future__ import annotations

from pydantic import BaseModel


class UploadTaskFilePayload(BaseModel):
    filename: str
    content_type: str | None = None
    content_base64: str
