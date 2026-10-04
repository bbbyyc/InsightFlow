"""Read-only check: compare stored vectors with freshly encoded source text."""
import asyncio
import json
from pathlib import Path
import numpy as np
from sqlalchemy import select, func
from app.database import async_session, engine
from app.models.chunk import Chunk
from app.models.document import Document
from app.services.embedding_service import EmbeddingService
from app.config import settings


async def main():
    async with async_session() as db:
        counts = (await db.execute(select(Chunk.embedding_model, func.count()).group_by(Chunk.embedding_model))).all()
        rows = (await db.execute(select(Chunk, Document.title).join(Document).where(
            Chunk.chunk_index == 2, Document.status == 'COMPLETED').order_by(Document.title))).all()
        rows = rows[:5] + [r for r in rows if r[1] == 'Vue3快速上手.md']
        vectors = await EmbeddingService().embed_batch([c.content for c, _ in rows])
        samples = []
        for (chunk, title), fresh in zip(rows, vectors):
            old = np.asarray(chunk.embedding)
            new = np.asarray(fresh)
            samples.append({'chunk_id': str(chunk.id), 'title': title, 'stored_model': chunk.embedding_model,
                            'cosine_similarity_to_fresh': float(np.dot(old, new)/(np.linalg.norm(old)*np.linalg.norm(new))),
                            'max_abs_delta': float(np.max(np.abs(old-new)))})
    result = {'current_model': settings.embedding_model, 'model_counts': dict(counts), 'samples': samples,
              'scope': 'small read-only sample, not full-corpus vector validation'}
    Path('/tmp/resume_embedding_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
