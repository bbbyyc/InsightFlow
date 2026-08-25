import re


def extract_citations(answer: str, chunks: list[dict]) -> list[dict]:
    """Map valid citation numbers to their immutable context chunks."""
    referenced = {int(value) for value in re.findall(r"\[(\d+)]", answer)}
    cited = []
    seen_chunk_ids = set()

    for context_index, chunk in enumerate(chunks, start=1):
        if context_index not in referenced:
            continue
        chunk_id = str(chunk.get("chunk_id", ""))
        if chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(chunk_id)
        cited.append({
            "citation_number": context_index,
            "document_title": chunk["document_title"],
            "document_id": chunk.get("document_id", ""),
            "section_title": chunk.get("section_title"),
            "content_snippet": chunk["content"][:200],
            "chunk_id": chunk_id,
        })
    return cited
