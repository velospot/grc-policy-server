import json
from datetime import datetime
from pathlib import Path

from grc_policy_server.models.domain import DocumentDomain

UPLOAD_ROOT = Path("/data/uploads")


## TO DO : save metadata about uploaded info in mongodb
# TO DO : remove metadata.json dependency on doc_dir
class DocumentRepository:
    def list_documents(self) -> list[DocumentDomain]:
        documents = []

        for doc_dir in UPLOAD_ROOT.iterdir():
            meta_file = doc_dir / "metadata.json"
            if not meta_file.exists():
                continue

            with open(meta_file) as f:
                meta = json.load(f)

            documents.append(
                DocumentDomain(
                    id=meta["id"],
                    name=meta["name"],
                    version=meta["version"],
                    upload_date=datetime.fromisoformat(
                        meta["upload_date"].replace("Z", "")
                    ),
                    size_bytes=meta["size_bytes"],
                    category=meta["category"],
                    file_path=str(doc_dir / "original.pdf"),
                )
            )

        return documents
