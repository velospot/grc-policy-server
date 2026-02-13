def semantic_chunk(docling_json: dict) -> list[dict]:
    chunks = []
    for section in docling_json.get("sections", []):
        for para in section.get("paragraphs", []):
            chunks.append(
                {
                    "text": para["text"],
                    "section_path": section["title"],
                    "level": section["level"],
                }
            )
    return chunks
