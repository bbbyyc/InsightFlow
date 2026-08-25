import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, Uuid, UniqueConstraint, func

from app.database import Base


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (UniqueConstraint("message_id", "citation_number", name="uq_citations_message_number"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    message_id = Column(Uuid, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    retrieval_record_id = Column(Uuid, ForeignKey("retrieval_records.id", ondelete="SET NULL"), nullable=True)
    chunk_id = Column(Uuid, ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True)
    document_id = Column(Uuid, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    citation_number = Column(Integer, nullable=False)
    document_title = Column(String(500), nullable=False)
    section_title = Column(String(500), nullable=True)
    content_snippet = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
