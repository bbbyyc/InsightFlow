from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.task_record import TaskRecord, TaskStatus


def utcnow():
    return datetime.now(timezone.utc)


class TaskService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_or_get(
        self,
        task_type: str,
        idempotency_key: str,
        resource_type: str | None = None,
        resource_id=None,
    ) -> tuple[TaskRecord, bool]:
        existing = await self.db.scalar(select(TaskRecord).where(TaskRecord.idempotency_key == idempotency_key))
        if existing:
            return existing, False
        record = TaskRecord(
            task_type=task_type,
            idempotency_key=idempotency_key,
            resource_type=resource_type,
            resource_id=resource_id,
            max_retries=settings.task_max_retries,
        )
        self.db.add(record)
        try:
            await self.db.commit()
        except IntegrityError:
            # A concurrent request may have won the idempotency-key race.
            await self.db.rollback()
            existing = await self.get_by_idempotency_key(idempotency_key)
            if existing:
                return existing, False
            raise
        await self.db.refresh(record)
        return record, True

    async def get_by_idempotency_key(self, idempotency_key: str) -> TaskRecord | None:
        return await self.db.scalar(
            select(TaskRecord).where(TaskRecord.idempotency_key == idempotency_key)
        )

    async def get(self, task_id: str | UUID) -> TaskRecord | None:
        try:
            return await self.db.get(TaskRecord, UUID(str(task_id)))
        except ValueError:
            return None

    async def mark_running(self, record: TaskRecord, attempt: int):
        record.status = TaskStatus.RUNNING
        record.attempt = attempt
        record.started_at = record.started_at or utcnow()
        record.error_type = None
        record.error_message = None
        await self.db.commit()

    async def mark_retrying(self, record: TaskRecord, exc: Exception):
        record.status = TaskStatus.RETRYING
        record.error_type = type(exc).__name__
        record.error_message = str(exc)[:4000]
        await self.db.commit()

    async def mark_progress(self, record: TaskRecord, progress: int, stage: str):
        record.progress = max(0, min(99, progress))
        record.result_summary = {**(record.result_summary or {}), "stage": stage}
        await self.db.commit()

    async def mark_succeeded(self, record: TaskRecord, summary: dict):
        record.status = TaskStatus.SUCCEEDED
        record.progress = 100
        record.result_summary = summary
        record.completed_at = utcnow()
        record.error_type = None
        record.error_message = None
        await self.db.commit()

    async def mark_failed(self, record: TaskRecord, exc: Exception):
        record.status = TaskStatus.FAILED
        record.error_type = type(exc).__name__
        record.error_message = str(exc)[:4000]
        record.completed_at = utcnow()
        await self.db.commit()


def serialize_task(record: TaskRecord) -> dict:
    return {
        "id": str(record.id),
        "task_type": record.task_type,
        "status": record.status.value if hasattr(record.status, "value") else str(record.status),
        "resource_type": record.resource_type,
        "resource_id": str(record.resource_id) if record.resource_id else None,
        "celery_task_id": record.celery_task_id,
        "attempt": record.attempt,
        "max_retries": record.max_retries,
        "progress": record.progress,
        "result_summary": record.result_summary or {},
        "error_type": record.error_type,
        "error_message": record.error_message,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
    }
