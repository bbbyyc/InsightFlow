import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./phase3_test.db")

from app.database import async_session
from app.models import Document, DocumentStatus, TaskStatus
from app.services.task_service import TaskService
from app.tasks import process_document_task


async def create_document_task(prefix: str):
    async with async_session() as db:
        document = Document(
            title=f"{prefix}.md", file_type="md", file_path="none",
            status=DocumentStatus.PENDING,
        )
        db.add(document)
        await db.commit()
        await db.refresh(document)
        record, _ = await TaskService(db).create_or_get(
            "document_processing", f"{prefix}-{uuid.uuid4()}", "document", document.id,
        )
        return str(record.id), str(document.id)


async def read_task(task_id: str):
    async with async_session() as db:
        record = await TaskService(db).get(task_id)
        return record.status, record.attempt, record.error_type, record.result_summary


class Phase3CeleryTests(unittest.TestCase):
    def test_success_and_repeated_delivery_are_idempotent(self):
        task_id, document_id = asyncio.run(create_document_task("celery-success"))
        with patch(
            "app.services.document_service.DocumentService.process_document",
            new=AsyncMock(return_value=None),
        ) as process:
            first = process_document_task.run(task_id, document_id)
            second = process_document_task.run(task_id, document_id)

        self.assertEqual(first["document_id"], document_id)
        self.assertEqual(second, first)
        self.assertEqual(process.await_count, 1)
        status, attempt, error_type, summary = asyncio.run(read_task(task_id))
        self.assertEqual(status, TaskStatus.SUCCEEDED)
        self.assertEqual(attempt, 1)
        self.assertIsNone(error_type)
        self.assertEqual(summary["document_id"], document_id)

    def test_retry_state_and_error_are_persisted(self):
        task_id, document_id = asyncio.run(create_document_task("celery-retry"))
        with (
            patch(
                "app.services.document_service.DocumentService.process_document",
                new=AsyncMock(side_effect=RuntimeError("temporary broker-independent failure")),
            ),
            # Calling .run() directly has no Celery worker request context, so
            # Celery re-raises the original exception after scheduling logic.
            self.assertRaises(RuntimeError),
        ):
            process_document_task.run(task_id, document_id)

        status, attempt, error_type, _ = asyncio.run(read_task(task_id))
        self.assertEqual(status, TaskStatus.RETRYING)
        self.assertEqual(attempt, 1)
        self.assertEqual(error_type, "RuntimeError")

    def test_terminal_failure_is_persisted_after_retry_budget(self):
        task_id, document_id = asyncio.run(create_document_task("celery-failure"))
        with (
            patch.object(process_document_task, "max_retries", 0),
            patch(
                "app.services.document_service.DocumentService.process_document",
                new=AsyncMock(side_effect=ValueError("permanent parse failure")),
            ),
            self.assertRaises(ValueError),
        ):
            process_document_task.run(task_id, document_id)

        status, attempt, error_type, _ = asyncio.run(read_task(task_id))
        self.assertEqual(status, TaskStatus.FAILED)
        self.assertEqual(attempt, 1)
        self.assertEqual(error_type, "ValueError")


if __name__ == "__main__":
    unittest.main()
