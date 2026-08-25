from sqlalchemy import Column, String, DateTime, Text, ForeignKey, func, Uuid, JSON
import uuid

from app.database import Base


class Message(Base):
    __tablename__ = "messages"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id = Column(Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    citations = Column(JSON, default=list)
    retrieval_record_id = Column(Uuid, ForeignKey("retrieval_records.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
