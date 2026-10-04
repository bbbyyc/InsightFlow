"""Container-only controlled service benchmarks; no production throughput claims."""
import asyncio
import hashlib
import json
import math
import platform
import random
import statistics
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select, delete
from app.database import async_session, engine
from app.models import Document, DocumentStatus, Chunk
from app.services.document_service import DocumentService
from app.services.embedding_service import EmbeddingService
from app.services.bm25_service import BM25Service
from app.config import settings


def stats(values):
    return {'p50_ms': statistics.median(values),
            'p95_ms': sorted(values)[math.ceil(len(values)*.95)-1]}


async def main():
    report = {'scope': 'local PostgreSQL service calls, serial, warm process; no HTTP/queue/LLM latency',
              'platform': platform.platform(), 'model': settings.embedding_model,
              'batch_size': settings.embedding_batch_size, 'recovery_pairs': [], 'bm25_pairs': []}
    rng = random.Random(20260921)
    async with async_session() as db:
        source_rows = (await db.execute(select(Chunk.content).join(Document).where(
            Document.status == DocumentStatus.COMPLETED).order_by(Chunk.id))).scalars().all()
    report['source_chunks'] = len(source_rows)
    report['source_sha256'] = hashlib.sha256(json.dumps(source_rows, ensure_ascii=False).encode()).hexdigest()
    # Real-corpus BM25 comparison. Each pair uses identical query and scope.
    queries = ['文档处理', '冲激响应', '图像灰度', 'Vue reactive', 'SQL SELECT',
               '水印攻击', 'FTP 服务', '卷积积分', '信息隐藏', '阶跃响应']
    async def search(query, rebuild):
        start = time.perf_counter()
        async with async_session() as db:
            service = BM25Service(db)
            if rebuild:
                service.mark_dirty()
            rows = await service.search(query, top_k=10)
        return {'ms': (time.perf_counter()-start)*1000,
                'ranking': [(r.chunk_id, r.score) for r in rows]}
    await search(queries[0], False)
    for i in range(50):
        modes = ['forced_rebuild', 'cached']
        rng.shuffle(modes)
        pair = {'query': queries[i % len(queries)]}
        for mode in modes:
            pair[mode] = await search(pair['query'], mode == 'forced_rebuild')
        assert pair['cached']['ranking'] == pair['forced_rebuild']['ranking']
        report['bm25_pairs'].append(pair)
        if i % 10 == 9:
            print(f'BM25 pairs: {i+1}/50', flush=True)
    report['bm25'] = {mode: stats([p[mode]['ms'] for p in report['bm25_pairs']])
                      for mode in ('cached', 'forced_rebuild')}
    report['bm25']['p95_reduction_percent'] = 100*(1-report['bm25']['cached']['p95_ms']/report['bm25']['forced_rebuild']['p95_ms'])
    report['bm25']['identical_rankings'] = True
    # A synthetic Markdown fixture made from 128 real-corpus excerpts. Not a
    # claim about PDF/OCR recovery, nor about independent real business files.
    content = '\n\n'.join(f'## Evidence {i}\n{source_rows[i % len(source_rows)][:700].replace(chr(35), "")}' for i in range(128))
    report['recovery_fixture_sha256'] = hashlib.sha256(content.encode()).hexdigest()
    await EmbeddingService().embed_single('模型预热')
    original_embed = EmbeddingService.embed
    for trial in range(3):
        modes = ['full_restart', 'resume']
        rng.shuffle(modes)
        pair = {'trial': trial, 'order': modes, 'arms': {}}
        for mode in modes:
            with tempfile.TemporaryDirectory(prefix='resume-benchmark-', dir='/app/data/uploads') as folder:
                source = Path(folder)/'fixture.md'
                source.write_text(content, encoding='utf-8')
                async with async_session() as db:
                    doc = Document(title='resume-benchmark-'+uuid4().hex+'.md', file_type='md',
                                   file_path=str(source), status=DocumentStatus.PENDING)
                    db.add(doc)
                    await db.commit()
                    doc_id = doc.id
                counts = {'before_failure': 0, 'during_recovery': 0}
                try:
                    async def failing_embed(service, texts):
                        if counts['before_failure'] >= 3*settings.embedding_batch_size:
                            raise RuntimeError('benchmark injected interruption after three batches')
                        result = await original_embed(service, texts)
                        counts['before_failure'] += len(texts)
                        return result
                    with patch.object(EmbeddingService, 'embed', failing_embed):
                        async with async_session() as db:
                            try:
                                await DocumentService(db).process_document(str(doc_id))
                            except RuntimeError as exc:
                                assert str(exc).startswith('benchmark injected')
                            else:
                                raise AssertionError('Expected injected failure')
                    async with async_session() as db:
                        chunks = (await db.scalars(select(Chunk).where(Chunk.document_id==doc_id))).all()
                        before_ids = {str(c.id) for c in chunks}
                        persisted = sum(c.embedding is not None for c in chunks)
                    async def counting_embed(service, texts):
                        result = await original_embed(service, texts)
                        counts['during_recovery'] += len(texts)
                        return result
                    start = time.perf_counter()
                    with patch.object(EmbeddingService, 'embed', counting_embed):
                        async with async_session() as db:
                            if mode == 'full_restart':
                                await db.execute(delete(Chunk).where(Chunk.document_id==doc_id))
                                await db.commit()
                            await DocumentService(db).process_document(str(doc_id))
                    elapsed = (time.perf_counter()-start)*1000
                    async with async_session() as db:
                        chunks = (await db.scalars(select(Chunk).where(Chunk.document_id==doc_id))).all()
                        assert len(chunks) == len(before_ids) and all(c.embedding is not None for c in chunks)
                        if mode == 'resume':
                            assert {str(c.id) for c in chunks} == before_ids
                    pair['arms'][mode] = {'recovery_ms': elapsed, 'chunks': len(chunks),
                                          'persisted_vectors_after_failure': persisted, **counts}
                finally:
                    async with async_session() as db:
                        await DocumentService(db).delete_document(str(doc_id))
        report['recovery_pairs'].append(pair)
        print(f'Recovery pair: {trial+1}/3', flush=True)
    full = statistics.median(p['arms']['full_restart']['recovery_ms'] for p in report['recovery_pairs'])
    resumed = statistics.median(p['arms']['resume']['recovery_ms'] for p in report['recovery_pairs'])
    report['recovery'] = {'full_restart_median_ms': full, 'resume_median_ms': resumed,
                          'median_time_reduction_percent': 100*(1-resumed/full),
                          'embedding_count_reduction_percent': 100*(1-sum(p['arms']['resume']['during_recovery'] for p in report['recovery_pairs'])/sum(p['arms']['full_restart']['during_recovery'] for p in report['recovery_pairs']))}
    Path('/tmp/resume_percentages.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'bm25':report['bm25'], 'recovery':report['recovery']}, indent=2), flush=True)
    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
