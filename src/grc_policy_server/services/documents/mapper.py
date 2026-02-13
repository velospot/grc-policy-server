from grc_policy_server.models.domain import DocumentDomain
from grc_policy_server.models.schemas import Document


def bytes_to_human(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024**2:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024**2):.1f} MB"


def to_document_response(doc: DocumentDomain) -> Document:
    return Document(
        id=doc.id,
        name=doc.name,
        version=doc.version,
        uploadDate=doc.upload_date.strftime("%Y-%m-%d"),
        size=bytes_to_human(doc.size_bytes),
        category=doc.category,
    )
