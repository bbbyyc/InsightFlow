from typing import List, Optional
from dataclasses import dataclass, field
import asyncio
from weakref import WeakKeyDictionary

import jieba
from rank_bm25 import BM25Okapi
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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


@dataclass
class _Snapshot:
    revision: int
    chunks: list[dict]
    index: BM25Okapi | None


@dataclass
class _Cache:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    snapshot: _Snapshot | None = None


# Separate databases and event loops must never share locks or corpus data.
_caches = WeakKeyDictionary()


class BM25Service:

    def __init__(self, db: AsyncSession):
        self.db = db
        self._dirty = False

    def _cache(self):
        engine = self.db.get_bind().engine
        loops = _caches.setdefault(engine, WeakKeyDictionary())
        return loops.setdefault(asyncio.get_running_loop(), _Cache())

    async def _revision(self):
        return int(await self.db.scalar(text("SELECT revision FROM corpus_revision WHERE id = 1")))

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
                WHERE d.status = 'COMPLETED'
                ORDER BY c.document_id, c.chunk_index
            """)
        )
        rows = result.fetchall()
        chunks = [
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
        def build():
            tokenized = [self._tokenize(c["content"]) for c in chunks]
            return BM25Okapi(tokenized) if any(tokenized) else None

        return chunks, await asyncio.to_thread(build)

    async def _ensure_index(self):
        cache = self._cache()
        async with cache.lock:
            revision = await self._revision()
            if not self._dirty and cache.snapshot is not None and cache.snapshot.revision == revision:
                return cache.snapshot
            # Never publish a snapshot labelled with a newer revision than its rows.
            for _ in range(3):
                chunks, index = await self._load_chunks()
                after = await self._revision()
                if revision == after:
                    cache.snapshot = _Snapshot(revision, chunks, index)
                    self._dirty = False
                    return cache.snapshot
                revision = after
            raise RuntimeError("Corpus changed repeatedly during BM25 index build; retry the search")

    def mark_dirty(self):
        self._dirty = True

    async def search(
        self,
        query: str,
        top_k: int = 10,
        document_ids: Optional[List[str]] = None,
        per_document_limit: int | None = None,
    ) -> List[BM25Result]:
        snapshot = await self._ensure_index()
        if snapshot.index is None or top_k <= 0:
            return []

        return await asyncio.to_thread(
            self._search_snapshot, snapshot, query, top_k, document_ids, per_document_limit,
        )

    def _search_snapshot(self, snapshot, query, top_k, document_ids, per_document_limit):

        tokens = self._tokenize(query)
        if not tokens:
            return []

        # Build filter mask for document_ids
        mask = None
        if document_ids:
            doc_set = set(document_ids)
            mask = [i for i, c in enumerate(snapshot.chunks) if c["document_id"] in doc_set]

        if mask is not None:
            if not mask:
                return []
            # Keep corpus-level IDF statistics stable and apply document scope
            # only as a candidate filter. Rebuilding on a two-document scope
            # makes terms present in exactly one document receive an IDF of 0.
            scores = snapshot.index.get_scores(tokens)
            indexed = [(i, float(scores[i])) for i in mask if scores[i] > 0]
        else:
            scores = snapshot.index.get_scores(tokens)
            indexed = [(i, float(s)) for i, s in enumerate(scores) if s > 0]

        indexed.sort(key=lambda x: x[1], reverse=True)

        selected: list[tuple[int, float]] = []
        per_doc_counts: dict[str, int] = {}
        for i, score in indexed:
            document_id = snapshot.chunks[i]["document_id"]
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
            c = snapshot.chunks[i]
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
