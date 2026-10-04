"""Verify BM25 invalidation using real PostgreSQL; remove only our own fixture."""
import asyncio
import json
from uuid import uuid4

from sqlalchemy import delete, update
from app.database import async_session, engine
from app.models import Document, Chunk
from app.services.bm25_service import BM25Service


async def main():
    document_id = uuid4()
    term = 'cacheprobe' + uuid4().hex
    replacement = 'cachechanged' + uuid4().hex
    async def search(query):
        async with async_session() as db:
            service = BM25Service(db)
            rows = await service.search(query, document_ids=[str(document_id)])
            return rows, service._cache().snapshot

    try:
        async with async_session() as db:
            db.add(Document(id=document_id, title='BM25 acceptance fixture', file_type='txt', file_path='unused'))
            await db.flush()
            for i, content in enumerate([term, 'forest cedar', 'ocean coral']):
                db.add(Chunk(document_id=document_id, chunk_index=i, content=content))
            await db.commit()
        first, snapshot = await search(term)
        assert first, 'insert not visible'
        repeated = await asyncio.gather(*(search(term) for _ in range(12)))
        assert all(rows == first and cached is snapshot for rows, cached in repeated)
        async with async_session() as db:
            await db.execute(update(Chunk).where(Chunk.document_id == document_id, Chunk.chunk_index == 0).values(content=replacement))
            await db.execute(update(Document).where(Document.id == document_id).values(title='Updated fixture'))
            await db.commit()
        old, _ = await search(term)
        new, updated_snapshot = await search(replacement)
        assert not old and new[0].document_title == 'Updated fixture'
        assert updated_snapshot.revision > snapshot.revision
        async with async_session() as db:
            await db.execute(delete(Document).where(Document.id == document_id))
            await db.commit()
        deleted, _ = await search(replacement)
        assert not deleted, 'deleted evidence still returned'
        print(json.dumps({'postgres_bm25': 'passed', 'concurrent_requests': 12,
                          'checks': ['insert', 'reuse', 'external_session_update', 'title_update', 'delete']}))
    finally:
        async with async_session() as db:
            await db.execute(delete(Document).where(Document.id == document_id))
            await db.commit()
        await engine.dispose()


asyncio.run(main())
