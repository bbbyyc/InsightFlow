import enum
import uuid

from sqlalchemy import Column, DateTime, Enum, Integer, JSON, String, Text, Uuid, func

from app.database import Base


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskRecord(Base):
    __tablename__ = "task_records"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    task_type = Column(String(50), nullable=False, index=True)
    status = Column(Enum(TaskStatus), nullable=False, default=TaskStatus.PENDING, index=True)
    idempotency_key = Column(String(255), nullable=False, unique=True)
    celery_task_id = Column(String(255), nullable=True, unique=True)
    resource_type = Column(String(50), nullable=True)
    resource_id = Column(Uuid, nullable=True, index=True)
    attempt = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    progress = Column(Integer, nullable=False, default=0)
    result_summary = Column(JSON, nullable=False, default=dict)
    error_type = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
