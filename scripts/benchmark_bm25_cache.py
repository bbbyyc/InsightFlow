"""Isolated local benchmark: forced rebuild versus revision-checked reuse.

Run with the backend dependencies installed. Does not contact a model or alter
the configured application database. Results are not HTTP or production metrics.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


async def benchmark(url, count, requests):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models import Chunk, Document
    from app.services.bm25_service import BM25Service

    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            doc = Document(title='Benchmark', file_type='md', file_path='unused')
            db.add(doc)
            await db.flush()
            for i in range(count):
                db.add(Chunk(document_id=doc.id, chunk_index=i,
                             content=f'项目编号 item{i} 检索缓存 文档处理 性能测试 ' * 8))
            await db.commit()

        async def request(i, rebuild):
            start = perf_counter()
            async with sessions() as db:
                service = BM25Service(db)
                if rebuild:
                    service.mark_dirty()
                rows = await service.search(f'item{i % count}', top_k=5)
            return (perf_counter() - start) * 1000, [(r.chunk_id, r.score) for r in rows]

        await request(0, False)  # Exclude initial tokenizer/model-free warmup.
        report = {'corpus_chunks': count, 'requests_per_mode': requests,
                  'scope': 'SQLite service benchmark; warm tokenizer; no HTTP, embedding or LLM'}
        reference = None
        for name, rebuild in [('forced_rebuild', True), ('cached', False)]:
            results = [await request(i, rebuild) for i in range(requests)]
            timings = sorted(r[0] for r in results)
            ranking = [r[1] for r in results]
            if reference is None:
                reference = ranking
            else:
                assert ranking == reference, 'Ranking changed'
            report[name] = {'p50_ms': round(statistics.median(timings), 2),
                            'p95_ms': round(timings[min(len(timings)-1, int(len(timings)*.95))], 2)}
        report['identical_rankings'] = True
        return report
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chunks', type=int, default=1000)
    parser.add_argument('--requests', type=int, default=50)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.chunks < 3 or args.requests < 1:
        parser.error('Use at least 3 chunks and 1 request')
    with tempfile.TemporaryDirectory() as directory:
        url = 'sqlite+aiosqlite:///' + (Path(directory) / 'benchmark.db').as_posix()
        os.environ['DATABASE_URL'] = url
        subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'],
                       cwd=ROOT / 'backend', check=True, capture_output=True)
        report = asyncio.run(benchmark(url, args.chunks, args.requests))
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + '\n', encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
