from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy import func, select

from app.database import async_session
from app.models.chunk import Chunk
from app.services.document_service import DocumentService
from app.services.ids import require_uuid


async def main(document_id: str) -> None:
    parsed_id = require_uuid(document_id)
    async with async_session() as db:
        before = await db.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == parsed_id))
        before_ids = list((await db.scalars(select(Chunk.id).where(Chunk.document_id == parsed_id).order_by(Chunk.chunk_index))).all())
        await DocumentService(db).process_document(document_id)
        after = await db.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == parsed_id))
        after_ids = list((await db.scalars(select(Chunk.id).where(Chunk.document_id == parsed_id).order_by(Chunk.chunk_index))).all())
    print(json.dumps({
        "document_id": document_id,
        "chunk_count_before": before,
        "chunk_count_after": after,
        "same_chunk_ids": before_ids == after_ids,
        "duplicate_chunks": max(0, (after or 0) - (before or 0)),
    }))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
