import uuid

from sqlalchemy import Column, DateTime, Float, Integer, JSON, String, Text, Uuid, func

from app.database import Base


class RetrievalRecord(Base):
    __tablename__ = "retrieval_records"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id = Column(Uuid, nullable=True, index=True)
    query = Column(Text, nullable=False)
    rewritten_query = Column(Text, nullable=True)
    mode = Column(String(20), nullable=False, default="hybrid")
    document_ids = Column(JSON, nullable=False, default=list)
    result_chunk_ids = Column(JSON, nullable=False, default=list)
    diagnostics = Column(JSON, nullable=False, default=dict)
    top_k = Column(Integer, nullable=False, default=5)
    latency_ms = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="running")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
