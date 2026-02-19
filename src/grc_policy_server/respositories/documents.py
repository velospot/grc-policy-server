import json
import os
from datetime import datetime
from pathlib import Path

from grc_policy_server.models.domain import DocumentDomain

UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT", "/data/uploads"))


## TO DO : save metadata about uploaded info in mongodb
# TO DO : remove metadata.json dependency on doc_dir
class DocumentRepository:
    def __init__(self, upload_root: Path = UPLOAD_ROOT):
        self.upload_root = upload_root

    def list_documents(self) -> list[DocumentDomain]:
        documents = []

        if not self.upload_root.exists():
            return documents

        for doc_dir in self.upload_root.iterdir():
            if not doc_dir.is_dir():
                continue
            meta_file = doc_dir / "metadata.json"
            if not meta_file.exists():
                continue

            with open(meta_file) as f:
                meta = json.load(f)

            upload_date = meta.get("upload_date")
            if not upload_date:
                continue

            doc_id = meta.get("id")
            doc_name = meta.get("name")
            if not doc_id or not doc_name:
                continue

            documents.append(
                DocumentDomain(
                    id=doc_id,
                    name=doc_name,
                    version=meta.get("version", "unknown"),
                    upload_date=datetime.fromisoformat(
                        upload_date.replace("Z", "")
                    ),
                    size_bytes=meta.get("size_bytes", 0),
                    category=meta.get("category", "unknown"),
                    file_path=str(
                        doc_dir / meta.get("stored_filename", "original.pdf")
                    ),
                )
            )

        return documents
