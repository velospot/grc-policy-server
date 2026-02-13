from docling.document_converter import DocumentConverter


class DoclingService:
    def extract(self, file_path: str) -> dict:
        converter = DocumentConverter()
        doc = converter.convert(file_path)
        return doc.export_to_dict()
