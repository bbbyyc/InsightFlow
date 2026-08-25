import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from celery import Celery
from celery.exceptions import MaxRetriesExceededError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.services.ids import require_uuid


celery_app = Celery("insightflow", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json", accept_content=["json"], result_serializer="json",
    timezone="UTC", enable_utc=True, task_track_started=True,
    task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, task_soft_time_limit=3500, task_time_limit=3600,
)


def _run_async(coro_factory):
    async def runner():
        connect_args = {"statement_cache_size": 0} if settings.database_url.startswith("postgresql") else {}
        engine_options = {"poolclass": NullPool} if settings.database_url.startswith("sqlite") else {}
        engine = create_async_engine(settings.database_url, connect_args=connect_args, **engine_options)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with session_factory() as session:
                return await coro_factory(session)
        finally:
            await engine.dispose()
    return asyncio.run(runner())


@celery_app.task(bind=True, name="process_document", max_retries=settings.task_max_retries)
def process_document_task(self, task_id: str, document_id: str):
    from app.services.document_service import DocumentService
    from app.services.task_service import TaskService

    async def work(session):
        task_service = TaskService(session)
        record = await task_service.get(task_id)
        if not record:
            raise ValueError(f"Task {task_id} not found")
        if record.status.value == "succeeded":
            return record.result_summary
        await task_service.mark_running(record, self.request.retries + 1)
        try:
            await task_service.mark_progress(record, 5, "queued")

            async def progress(value, stage):
                await task_service.mark_progress(record, value, stage)

            await DocumentService(session).process_document(document_id, on_progress=progress)
            document = await DocumentService(session).get_document(document_id)
            summary = {"document_id": document_id, "chunk_count": document.chunk_count}
            await task_service.mark_succeeded(record, summary)
            return summary
        except Exception as exc:
            # A flush/database error leaves the transaction unusable. Roll it
            # back before persisting the retry or terminal failure state.
            await session.rollback()
            record = await task_service.get(task_id)
            if self.request.retries < self.max_retries:
                await task_service.mark_retrying(record, exc)
            else:
                await task_service.mark_failed(record, exc)
            raise

    try:
        return _run_async(work)
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=settings.task_retry_backoff_seconds * (2 ** self.request.retries))
        raise


@celery_app.task(bind=True, name="run_evaluation", max_retries=settings.task_max_retries)
def run_evaluation_task(self, task_id: str, evaluation_run_id: str):
    from app.models.evaluation_run import EvaluationRun
    from app.services.task_service import TaskService

    async def prepare(session):
        task_service = TaskService(session)
        record = await task_service.get(task_id)
        if not record:
            raise ValueError(f"Task {task_id} not found")
        if record.status.value == "succeeded":
            return {"already_completed": True, **(record.result_summary or {})}
        run = await session.get(EvaluationRun, require_uuid(evaluation_run_id))
        if not run:
            raise ValueError(f"Evaluation {evaluation_run_id} not found")
        await task_service.mark_running(record, self.request.retries + 1)
        run.status = "running"
        await session.commit()
        return {"dataset_path": run.dataset_path, "output_dir": run.output_dir, "parameters": run.parameters}

    prepared = _run_async(prepare)
    if prepared.get("already_completed"):
        return prepared
    eval_script = Path(os.getenv("EVALUATION_SCRIPT", "/eval/run_eval.py"))
    if not eval_script.is_file():
        eval_script = Path(__file__).resolve().parents[2] / "eval" / "run_eval.py"
    command = [sys.executable, str(eval_script), "--dataset", prepared["dataset_path"], "--output-dir", prepared["output_dir"], "--top-k", str(prepared["parameters"]["top_k"]), "--seed", str(prepared["parameters"]["seed"])]
    if prepared["parameters"].get("include_generation"):
        command.append("--include-generation")
    if prepared["parameters"].get("llm_judge"):
        command.append("--llm-judge")
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=3600)
        if completed.returncode:
            raise RuntimeError(f"evaluation exit {completed.returncode}: {completed.stderr[-2000:]}")
        summary_path = Path(prepared["output_dir"]) / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        async def succeed(session):
            run = await session.get(EvaluationRun, require_uuid(evaluation_run_id))
            record = await TaskService(session).get(task_id)
            run.status = "completed"
            run.summary = summary
            run.completed_at = datetime.now(timezone.utc)
            await TaskService(session).mark_succeeded(record, {"evaluation_run_id": evaluation_run_id, "output_dir": prepared["output_dir"]})
        _run_async(succeed)
        return summary
    except Exception as exc:
        async def fail(session):
            run = await session.get(EvaluationRun, require_uuid(evaluation_run_id))
            record = await TaskService(session).get(task_id)
            run.status = "failed"
            run.error_message = f"{type(exc).__name__}: {exc}"[:4000]
            run.completed_at = datetime.now(timezone.utc)
            if self.request.retries < self.max_retries:
                await TaskService(session).mark_retrying(record, exc)
            else:
                await TaskService(session).mark_failed(record, exc)
            await session.commit()
        _run_async(fail)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=settings.task_retry_backoff_seconds * (2 ** self.request.retries))
        raise
