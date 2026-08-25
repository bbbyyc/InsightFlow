from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, func, Uuid, UniqueConstraint, JSON
from pgvector.sqlalchemy import Vector
import uuid

from app.database import Base


class Chunk(Base):
    __tablename__ = "chunks"

    __table_args__ = (UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_index"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id = Column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(384).with_variant(JSON, "sqlite"), nullable=True)
    embedding_model = Column(String(255), nullable=True)
    chunk_index = Column(Integer, nullable=False)
    page_number = Column(Integer, nullable=True)
    section_title = Column(String(500), nullable=True)
    token_count = Column(Integer, default=0)
    content_sha256 = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
