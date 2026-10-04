from typing import List
import pdfplumber
import time

from app.parsers.base import BaseParser, ChunkData
from app.parsers.quality import DocumentQualityError, validate_extracted_text
from app.parsers.ocr import recognize_page
from app.config import settings


class ParsedPDFText(str):
    def __new__(cls, text, report):
        instance = super().__new__(cls, text)
        instance.report = report
        return instance


class PDFParser(BaseParser):

    def parse(self, file_path: str) -> str:
        texts: List[str] = []
        report = {"version": 1, "status": "processing", "pages": [], "ocr_pages": 0,
                  "ocr_languages": settings.pdf_ocr_languages}
        started = time.monotonic()
        with pdfplumber.open(file_path) as pdf:
            if len(pdf.pages) > settings.pdf_max_pages:
                raise DocumentQualityError(f"PDF 超过页数上限 {settings.pdf_max_pages}")
            for number, page in enumerate(pdf.pages, start=1):
                page_started = time.monotonic()
                page_text = page.extract_text() or ""
                reason = None
                try:
                    if page_text.strip():
                        validate_extracted_text(page_text, f"PDF 第 {number} 页")
                except DocumentQualityError as exc:
                    reason = str(exc)
                if not page_text.strip() and (getattr(page, "images", []) or getattr(page, "chars", []) or getattr(page, "curves", [])):
                    reason = "页面包含可见对象，但未提取到文本"
                entry = {"page_number": number, "method": "native" if page_text.strip() else "blank"}
                if reason:
                    entry.update(method="ocr", trigger=reason)
                    try:
                        if not settings.pdf_ocr_enabled:
                            raise DocumentQualityError(f"PDF 第 {number} 页需要 OCR，但自动 OCR 已关闭：{reason}")
                        remaining = settings.pdf_ocr_total_timeout - (time.monotonic()-started)
                        if report["ocr_pages"] >= settings.pdf_ocr_max_pages or remaining <= 0:
                            raise DocumentQualityError("PDF OCR 超过页数或总耗时上限，请拆分文件后上传。")
                        report["ocr_pages"] += 1
                        result = recognize_page(file_path, number, timeout=min(settings.pdf_ocr_page_timeout, remaining),
                                                languages=settings.pdf_ocr_languages,
                                                min_confidence=settings.pdf_ocr_min_confidence)
                        page_text = result.pop("text")
                        if result["method"] != "blank":
                            validate_extracted_text(page_text, f"PDF 第 {number} 页 OCR 结果")
                        entry.update(result)
                    except DocumentQualityError as exc:
                        entry.update(status="failed", error=str(exc))
                        report["pages"].append(entry)
                        report.update(status="failed", elapsed_ms=round((time.monotonic()-started)*1000))
                        exc.report = report
                        raise
                entry.update(characters=len(page_text), elapsed_ms=round((time.monotonic()-page_started)*1000))
                report["pages"].append(entry)
                # Preserve empty pages too: later citations must keep physical page numbers.
                texts.append(page_text)
        # Keep page boundaries so citations can return to a real PDF page.
        text = "\n\f\n".join(texts)
        validate_extracted_text(text, "PDF")
        report.update(status="completed", elapsed_ms=round((time.monotonic()-started)*1000))
        return ParsedPDFText(text, report)

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
