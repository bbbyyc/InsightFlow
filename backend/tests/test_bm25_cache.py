"""Real SQLite migrations/transactions; no mocked database or ranking."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Chunk, Document, DocumentStatus
from app.services.bm25_service import BM25Service


class BM25CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = 'sqlite+aiosqlite:///' + (Path(self.temp.name) / 'cache.db').as_posix()
        subprocess.run(
            [sys.executable, '-m', 'alembic', 'upgrade', 'head'],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'DATABASE_URL': self.url},
            check=True, capture_output=True,
        )
        self.engine = create_async_engine(self.url)
        self.writer_engine = create_async_engine(self.url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.writers = async_sessionmaker(self.writer_engine, expire_on_commit=False)
        async with self.writers() as db:
            self.doc = Document(title='original', file_type='md', file_path='unused', status=DocumentStatus.COMPLETED)
            db.add(self.doc)
            await db.flush()
            for i, content in enumerate(['quartz crystal', 'ocean waves', 'forest trees']):
                db.add(Chunk(document_id=self.doc.id, chunk_index=i, content=content))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()
        await self.writer_engine.dispose()
        self.temp.cleanup()

    async def search(self, query='quartz'):
        async with self.sessions() as db:
            return await BM25Service(db).search(query)

    async def test_concurrent_requests_share_one_build_and_keep_ranking(self):
        original = BM25Service._load_chunks
        builds = []
        async def counted(service):
            builds.append(1)
            return await original(service)
        with patch.object(BM25Service, '_load_chunks', counted):
            results = await asyncio.gather(*(self.search() for _ in range(12)))
            self.assertEqual(len(builds), 1)
            self.assertTrue(all(result == results[0] for result in results))
            self.assertEqual(results[0][0].content, 'quartz crystal')
            async with self.sessions() as db:
                await BM25Service(db).rebuild_index()
            self.assertEqual(await self.search(), results[0])
            self.assertEqual(len(builds), 2)

    async def test_external_writer_updates_content_title_and_deletes(self):
        self.assertTrue(await self.search())
        async with self.writers() as db:
            # Deliberately do not update content_sha256: DB triggers cover raw writes too.
            await db.execute(update(Chunk).where(Chunk.chunk_index == 0).values(content='sapphire mineral'))
            await db.execute(update(Document).values(title='renamed'))
            await db.commit()
        self.assertEqual(await self.search(), [])
        self.assertEqual((await self.search('sapphire'))[0].document_title, 'renamed')
        async with self.writers() as db:
            await db.execute(delete(Document))
            await db.commit()
        self.assertEqual(await self.search('sapphire'), [])

    async def test_status_change_invalidates_cache_and_hides_unready_documents(self):
        self.assertTrue(await self.search())
        for status in [DocumentStatus.FAILED, DocumentStatus.PROCESSING, DocumentStatus.PENDING]:
            async with self.writers() as db:
                await db.execute(update(Document).values(status=status))
                await db.commit()
            self.assertEqual(await self.search(), [])
        async with self.writers() as db:
            await db.execute(update(Document).values(status=DocumentStatus.COMPLETED))
            await db.commit()
        self.assertTrue(await self.search())

    async def test_empty_snapshot_invalidates_on_insert(self):
        async with self.writers() as db:
            await db.execute(delete(Chunk))
            await db.commit()
        self.assertEqual(await self.search(), [])

        async with self.writers() as db:
            for i, content in enumerate(['quartz crystal', 'ocean waves', 'forest trees']):
                db.add(Chunk(document_id=self.doc.id, chunk_index=i, content=content))
            await db.commit()
        self.assertEqual((await self.search())[0].content, 'quartz crystal')

    async def test_rollback_does_not_change_cached_snapshot(self):
        expected = await self.search()
        async with self.writers() as db:
            await db.execute(delete(Chunk))
            await db.rollback()
        self.assertEqual(await self.search(), expected)

    async def test_blank_corpus_and_document_scope(self):
        found = await self.search()
        async with self.sessions() as db:
            service = BM25Service(db)
            self.assertEqual(await service.search('quartz', document_ids=['missing']), [])
            self.assertEqual(await service.search('quartz', document_ids=[found[0].document_id]), found)
        async with self.writers() as db:
            await db.execute(update(Chunk).values(content='   '))
            await db.commit()
        self.assertEqual(await self.search(), [])

    async def test_build_racing_committed_update_retries(self):
        original = BM25Service._load_chunks
        builds = []
        async def racing(service):
            result = await original(service)
            builds.append(1)
            if len(builds) == 1:
                async with self.writers() as db:
                    await db.execute(update(Chunk).where(Chunk.chunk_index == 0).values(content='sapphire mineral'))
                    await db.commit()
            return result
        with patch.object(BM25Service, '_load_chunks', racing):
            self.assertEqual(await self.search(), [])
            self.assertEqual(len(builds), 2)
        self.assertTrue(await self.search('sapphire'))
