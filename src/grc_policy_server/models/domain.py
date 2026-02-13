from dataclasses import dataclass
from datetime import datetime


@dataclass
class DocumentDomain:
    id: str
    name: str
    version: str
    upload_date: datetime
    size_bytes: int
    category: str
    file_path: str
