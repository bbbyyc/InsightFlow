from sqlalchemy import Column, ForeignKey, DateTime, func, Uuid, UniqueConstraint
import uuid

from app.database import Base


class ConversationDocument(Base):
    __tablename__ = "conversation_documents"
    __table_args__ = (UniqueConstraint("conversation_id", "document_id", name="uq_conversation_documents_pair"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id = Column(Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    document_id = Column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
