import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, Uuid, UniqueConstraint, func

from app.database import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id = Column(Uuid, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    query = Column(Text, nullable=False)
    document_ids = Column(JSON, nullable=False, default=list)
    status = Column(String(20), nullable=False, default="pending", index=True)
    current_node = Column(String(50), nullable=True)
    answer_summary = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)


class AgentNodeEvent(Base):
    __tablename__ = "agent_node_events"
    __table_args__ = (UniqueConstraint("agent_run_id", "sequence", name="uq_agent_node_events_run_sequence"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_run_id = Column(Uuid, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    node = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)
    duration_ms = Column(Float, nullable=True)
    input_summary = Column(JSON, nullable=False, default=dict)
    result_summary = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
