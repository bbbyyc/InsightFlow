import os
import uuid
import hashlib
from datetime import datetime, timezone
from typing import Awaitable, Callable, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document, DocumentStatus
from app.models.chunk import Chunk
from app.parsers.base import ChunkData
from app.services.parser_registry import get_parser
from app.services.embedding_service import EmbeddingService
from app.services.ids import parse_uuid, require_uuid


class DocumentService:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_document(self, filename: str, file_type: str) -> Document:
        file_id = str(uuid.uuid4())
        ext = file_type or filename.rsplit(".", 1)[-1].lower()
        save_path = os.path.join(settings.upload_dir, f"{file_id}.{ext}")

        doc = Document(
            title=filename,
            file_type=ext,
            file_path=save_path,
            status=DocumentStatus.PENDING,
        )
        self.db.add(doc)
        await self.db.commit()
        await self.db.refresh(doc)
        return doc

    async def process_document(
        self,
        document_id: str,
        on_progress: Callable[[int, str], Awaitable[None]] | None = None,
    ):
        doc = await self.db.get(Document, require_uuid(document_id))
        if not doc:
            raise ValueError(f"Document {document_id} not found")

        doc.status = DocumentStatus.PROCESSING
        await self.db.commit()

        async def progress(value: int, stage: str):
            if on_progress:
                await on_progress(value, stage)

        try:
            await progress(10, "parsing")
            existing_chunks = await self.get_chunks(str(doc.id))
            if not existing_chunks:
                parser = get_parser(doc.file_type)
                text = parser.parse(doc.file_path)
                await progress(35, "chunking")
                chunk_data_list = parser.chunk(text, settings.chunk_size, settings.chunk_overlap)
                await self._save_chunks(doc.id, chunk_data_list)
                chunk_count = len(chunk_data_list)
            else:
                chunk_count = len(existing_chunks)

            await progress(65, "embedding")
            await self._generate_embeddings(doc.id)

            await progress(90, "indexing")

            doc.status = DocumentStatus.COMPLETED
            doc.chunk_count = chunk_count
            doc.error_message = None
            doc.processed_at = datetime.now(timezone.utc)
            await self.db.commit()
        except Exception as exc:
            doc.status = DocumentStatus.FAILED
            doc.error_message = f"{type(exc).__name__}: {exc}"[:4000]
            await self.db.commit()
            raise

    async def _save_chunks(self, document_id: uuid.UUID, chunk_data_list: List[ChunkData]):
        existing = {chunk.chunk_index: chunk for chunk in await self.get_chunks(str(document_id))}
        for cd in chunk_data_list:
            content_sha256 = hashlib.sha256(cd.content.encode("utf-8")).hexdigest()
            if cd.chunk_index in existing:
                chunk = existing[cd.chunk_index]
                chunk.content = cd.content
                chunk.page_number = cd.page_number
                chunk.section_title = cd.section_title
                chunk.token_count = cd.token_count
                chunk.content_sha256 = content_sha256
                continue
            chunk = Chunk(
                document_id=document_id,
                content=cd.content,
                chunk_index=cd.chunk_index,
                page_number=cd.page_number,
                section_title=cd.section_title,
                token_count=cd.token_count,
                content_sha256=content_sha256,
            )
            self.db.add(chunk)
        await self.db.commit()

    async def _generate_embeddings(self, document_id: uuid.UUID):
        result = await self.db.execute(
            select(Chunk).where(
                Chunk.document_id == document_id,
                (Chunk.embedding.is_(None)) | (Chunk.embedding_model != settings.embedding_model),
            ).order_by(Chunk.chunk_index)
        )
        chunks = list(result.scalars().all())
        if not chunks:
            return

        embed_service = EmbeddingService()
        texts = [c.content for c in chunks]
        embeddings = await embed_service.embed_batch(texts)

        for chunk, embedding in zip(chunks, embeddings):
            chunk.embedding = embedding
            chunk.embedding_model = settings.embedding_model
        await self.db.commit()

    async def get_document(self, document_id: str) -> Document | None:
        parsed = parse_uuid(document_id)
        return await self.db.get(Document, parsed) if parsed else None

    async def list_documents(self) -> List[Document]:
        result = await self.db.execute(select(Document).order_by(Document.created_at.desc()))
        return list(result.scalars().all())

    async def get_chunks(self, document_id: str) -> List[Chunk]:
        parsed = parse_uuid(document_id)
        if not parsed:
            return []
        result = await self.db.execute(
            select(Chunk).where(Chunk.document_id == parsed).order_by(Chunk.chunk_index)
        )
        return list(result.scalars().all())

    async def delete_document(self, document_id: str) -> bool:
        doc = await self.get_document(document_id)
        if not doc:
            return False
        file_path = doc.file_path
        await self.db.delete(doc)
        await self.db.commit()
        if file_path and os.path.isfile(file_path):
            os.remove(file_path)
        return True
