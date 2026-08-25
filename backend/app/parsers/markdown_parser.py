from typing import List

from app.parsers.base import BaseParser, ChunkData


class MarkdownParser(BaseParser):

    def parse(self, file_path: str) -> str:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    def chunk(self, text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[ChunkData]:
        sections = self._split_by_headers(text)
        return self._merge_sections(sections, chunk_size, chunk_overlap)

    def _split_by_headers(self, text: str) -> List[dict]:
        lines = text.split("\n")
        sections: List[dict] = []
        current_title = ""
        current_content: List[str] = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") and not stripped.startswith("####"):
                if current_content:
                    sections.append({"title": current_title, "content": "\n".join(current_content).strip()})
                current_title = stripped.lstrip("#").strip()
                current_content = []
            else:
                current_content.append(line)

        if current_content:
            sections.append({"title": current_title, "content": "\n".join(current_content).strip()})

        return sections

    def _merge_sections(self, sections: List[dict], chunk_size: int, chunk_overlap: int) -> List[ChunkData]:
        chunks: List[ChunkData] = []
        index = 0

        for section in sections:
            content = section["content"]
            title = section["title"]
            if not content:
                continue

            if len(content) <= chunk_size:
                chunks.append(ChunkData(
                    content=f"## {title}\n\n{content}" if title else content,
                    chunk_index=index,
                    section_title=title or None,
                    token_count=len(content)
                ))
                index += 1
            else:
                sub_chunks = self._sliding_window(content, title, chunk_size, chunk_overlap, index)
                chunks.extend(sub_chunks)
                index += len(sub_chunks)

        return chunks

    def _sliding_window(self, text: str, title: str, chunk_size: int, chunk_overlap: int, start_index: int) -> List[ChunkData]:
        chunks: List[ChunkData] = []
        step = chunk_size - chunk_overlap
        for i, start in enumerate(range(0, len(text), step)):
            end = min(start + chunk_size, len(text))
            chunk_text = text[start:end]
            prefix = f"## {title}\n\n" if title else ""
            chunks.append(ChunkData(
                content=prefix + chunk_text,
                chunk_index=start_index + i,
                section_title=title or None,
                token_count=len(chunk_text)
            ))
        return chunks
