import json
import shutil
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

    def _resolve_document_dir(self, document_id: str) -> Path:
        doc_id = document_id.strip()
        if not doc_id:
            raise ValueError("Document id must not be empty")

        upload_root = self.upload_root.resolve()
        document_dir = (upload_root / doc_id).resolve()
        if upload_root not in document_dir.parents:
            raise ValueError("Invalid document id")

        return document_dir

    def list_documents(self) -> list[DocumentDomain]:
        documents = []

        if not self.upload_root.exists():
            return documents

        try:
            doc_dirs = list(self.upload_root.iterdir())
        except OSError:
            return documents

        for doc_dir in doc_dirs:
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

    def get_document(self, document_id: str) -> DocumentDomain | None:
        """Return document metadata for a given id, or None if not found."""
        doc_dir = self._resolve_document_dir(document_id)
        meta_file = doc_dir / "metadata.json"
        if not meta_file.exists():
            return None
        meta = self._load_metadata(meta_file)
        if not meta:
            return None

        upload_date = meta.get("upload_date")
        doc_id = meta.get("id")
        doc_name = meta.get("name")
        if not upload_date or not doc_id or not doc_name:
            return None

        return DocumentDomain(
            id=doc_id,
            name=doc_name,
            version=meta.get("version", "unknown"),
            upload_date=datetime.fromisoformat(str(upload_date).replace("Z", "")),
            size_bytes=int(meta.get("size_bytes", 0) or 0),
            category=meta.get("category", "unknown"),
            file_path=str(doc_dir / meta.get("stored_filename", "original.pdf")),
        )

    def delete_document(self, document_id: str) -> bool:
        document_dir = self._resolve_document_dir(document_id)
        if not document_dir.exists():
            return False
        if not document_dir.is_dir():
            return False

        try:
            shutil.rmtree(document_dir)
        except FileNotFoundError:
            # Another concurrent request may already have removed this document.
            return False
        return True

    def resolve_pdf_path(
        self,
        *,
        document_id: str,
        filename: str | None = None,
    ) -> Path:
        document_dir = self._resolve_document_dir(document_id)
        if not document_dir.exists() or not document_dir.is_dir():
            raise FileNotFoundError("Document not found")

        requested_name = (filename or "").strip()
        if requested_name:
            return self._resolve_pdf_file(document_dir=document_dir, filename=requested_name)

        metadata = self._load_metadata(document_dir / "metadata.json") or {}
        stored_filename = str(metadata.get("stored_filename") or "").strip()
        if stored_filename:
            try:
                return self._resolve_pdf_file(
                    document_dir=document_dir,
                    filename=stored_filename,
                )
            except FileNotFoundError:
                pass

        for candidate in sorted(document_dir.glob("*.pdf")):
            if candidate.is_file():
                return candidate

        raise FileNotFoundError("No PDF file found for this document")

    @staticmethod
    def _resolve_pdf_file(*, document_dir: Path, filename: str) -> Path:
        candidate = (document_dir / filename).resolve()
        if document_dir not in candidate.parents:
            raise ValueError("Invalid PDF filename")
        if candidate.suffix.lower() != ".pdf":
            raise ValueError("Only PDF files can be downloaded from this endpoint")
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError("Requested PDF file was not found")
        return candidate
