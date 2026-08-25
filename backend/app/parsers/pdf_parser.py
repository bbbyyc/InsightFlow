from typing import List
import pdfplumber

from app.parsers.base import BaseParser, ChunkData


class PDFParser(BaseParser):

    def parse(self, file_path: str) -> str:
        texts: List[str] = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    texts.append(page_text)
        # Keep page boundaries so citations can return to a real PDF page.
        return "\n\f\n".join(texts)

    def chunk(self, text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[ChunkData]:
        chunks: List[ChunkData] = []
        for page_number, page_text in enumerate(text.split("\f"), start=1):
            paragraphs = [p.strip() for p in page_text.split("\n\n") if p.strip()]
            page_chunks = self._merge_paragraphs(paragraphs, chunk_size, chunk_overlap, page_number)
            for item in page_chunks:
                item.chunk_index = len(chunks)
                chunks.append(item)
        return chunks

    def _merge_paragraphs(self, paragraphs: List[str], chunk_size: int, chunk_overlap: int, page_number: int | None = None) -> List[ChunkData]:
        chunks: List[ChunkData] = []
        current = ""
        index = 0

        for para in paragraphs:
            # PDF extraction often returns an entire page as one paragraph. Split
            # oversized paragraphs first so a short PDF does not become one huge
            # chunk merely because it contains no blank lines.
            if len(para) > chunk_size:
                if current:
                    chunks.append(ChunkData(content=current, chunk_index=index, page_number=page_number, token_count=len(current)))
                    index += 1
                    current = ""
                step = max(1, chunk_size - chunk_overlap)
                for start in range(0, len(para), step):
                    text = para[start:start + chunk_size]
                    if not text.strip():
                        continue
                    chunks.append(ChunkData(content=text, chunk_index=index, page_number=page_number, token_count=len(text)))
                    index += 1
                    if start + chunk_size >= len(para):
                        break
                continue
            if len(current) + len(para) <= chunk_size:
                current = (current + "\n\n" + para).strip() if current else para
            else:
                if current:
                    chunks.append(ChunkData(
                        content=current,
                        chunk_index=index,
                        page_number=page_number,
                        token_count=len(current)
                    ))
                    index += 1
                    overlap_text = current[-chunk_overlap:] if len(current) > chunk_overlap else ""
                    current = overlap_text + "\n\n" + para if overlap_text else para
                else:
                    current = para

        if current.strip():
            chunks.append(ChunkData(
                content=current,
                chunk_index=index,
                page_number=page_number,
                token_count=len(current)
            ))
            index += 1

        return chunks
