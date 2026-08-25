from typing import List, Optional
from dataclasses import dataclass, field

import jieba
from rank_bm25 import BM25Okapi
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk


@dataclass
class BM25Result:
    chunk_id: str
    content: str
    document_id: str
    score: float
    chunk_index: int = 0
    page_number: int | None = None
    section_title: str | None = None
    document_title: str = ""
    document_type: str = ""


class BM25Service:

    def __init__(self, db: AsyncSession):
        self.db = db
        self._index: BM25Okapi | None = None
        self._chunks: list[dict] = []
        self._dirty = True

    def _tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        tokens = jieba.lcut(text)
        return [t.strip().lower() for t in tokens if t.strip()]

    async def _load_chunks(self):
        result = await self.db.execute(
            text("""
                SELECT c.id, c.content, c.document_id, c.chunk_index,
                       c.page_number, c.section_title,
                       d.title AS document_title, d.file_type AS document_type
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                ORDER BY c.document_id, c.chunk_index
            """)
        )
        rows = result.fetchall()
        self._chunks = [
            {
                "chunk_id": str(row[0]),
                "content": row[1],
                "document_id": str(row[2]),
                "chunk_index": row[3],
                "page_number": row[4],
                "section_title": row[5],
                "document_title": row[6],
                "document_type": row[7],
            }
            for row in rows
        ]
        if self._chunks:
            tokenized = [self._tokenize(c["content"]) for c in self._chunks]
            self._index = BM25Okapi(tokenized)
        self._dirty = False

    async def _ensure_index(self):
        if self._dirty or self._index is None:
            await self._load_chunks()

    def mark_dirty(self):
        self._dirty = True

    async def search(
        self,
        query: str,
        top_k: int = 10,
        document_ids: Optional[List[str]] = None,
        per_document_limit: int | None = None,
    ) -> List[BM25Result]:
        await self._ensure_index()
        if self._index is None:
            return []

        tokens = self._tokenize(query)
        if not tokens:
            return []

        # Build filter mask for document_ids
        mask = None
        if document_ids:
            doc_set = set(document_ids)
            mask = [i for i, c in enumerate(self._chunks) if c["document_id"] in doc_set]

        if mask is not None:
            if not mask:
                return []
            # Keep corpus-level IDF statistics stable and apply document scope
            # only as a candidate filter. Rebuilding on a two-document scope
            # makes terms present in exactly one document receive an IDF of 0.
            scores = self._index.get_scores(tokens)
            indexed = [(i, float(scores[i])) for i in mask if scores[i] > 0]
        else:
            scores = self._index.get_scores(tokens)
            indexed = [(i, float(s)) for i, s in enumerate(scores) if s > 0]

        indexed.sort(key=lambda x: x[1], reverse=True)

        selected: list[tuple[int, float]] = []
        per_doc_counts: dict[str, int] = {}
        for i, score in indexed:
            document_id = self._chunks[i]["document_id"]
            if per_document_limit and document_ids:
                count = per_doc_counts.get(document_id, 0)
                if count >= per_document_limit:
                    continue
                per_doc_counts[document_id] = count + 1
            selected.append((i, score))
            if len(selected) >= top_k:
                break

        results: List[BM25Result] = []
        for i, score in selected:
            c = self._chunks[i]
            results.append(BM25Result(
                chunk_id=c["chunk_id"],
                content=c["content"],
                document_id=c["document_id"],
                score=round(score, 4),
                chunk_index=c["chunk_index"],
                page_number=c["page_number"],
                section_title=c["section_title"],
                document_title=c["document_title"],
                document_type=c["document_type"],
            ))
        return results

    async def rebuild_index(self):
        self._dirty = True
        await self._ensure_index()
