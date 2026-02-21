import json
from datetime import datetime
from pathlib import Path

from grc_policy_server.core.config import settings
from grc_policy_server.models.domain import DocumentDomain

## TO DO : save metadata about uploaded info in mongodb
# TO DO : remove metadata.json dependency on doc_dir
class DocumentRepository:
    """Read uploaded document metadata from local storage."""

    def __init__(self, upload_root: Path | None = None):
        self.upload_root = upload_root or Path(settings.upload_root)

    @staticmethod
    def _load_metadata(metadata_path: Path) -> dict | None:
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

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

            meta = self._load_metadata(meta_file)
            if not meta:
                continue

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
                    upload_date=datetime.fromisoformat(upload_date.replace("Z", "")),
                    size_bytes=meta.get("size_bytes", 0),
                    category=meta.get("category", "unknown"),
                    file_path=str(
                        doc_dir / meta.get("stored_filename", "original.pdf")
                    ),
                )
            )

        return documents
