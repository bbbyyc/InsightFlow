import uuid

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text, Uuid, func

from app.database import Base


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id = Column(Uuid, ForeignKey("task_records.id", ondelete="SET NULL"), nullable=True)
    dataset_path = Column(String(1000), nullable=False)
    dataset_sha256 = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    parameters = Column(JSON, nullable=False, default=dict)
    output_dir = Column(String(1000), nullable=False)
    summary = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
