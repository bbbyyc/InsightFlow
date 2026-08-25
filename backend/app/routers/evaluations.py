import hashlib
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.models.evaluation_run import EvaluationRun
from app.services.task_service import TaskService, serialize_task
from app.tasks import run_evaluation_task
from app.services.ids import parse_uuid

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


def serialize_run(run: EvaluationRun) -> dict:
    return {
        "id": str(run.id), "task_id": str(run.task_id) if run.task_id else None,
        "status": run.status, "dataset_path": run.dataset_path,
        "dataset_sha256": run.dataset_sha256, "parameters": run.parameters,
        "output_dir": run.output_dir, "summary": run.summary,
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def read_json(path: Path, default):
    import json
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("")
async def list_evaluations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(50))
    return [serialize_run(run) for run in result.scalars().all()]


class EvaluationRequest(BaseModel):
    dataset_path: str = "/eval/datasets/candidate_project_v1.json"
    top_k: int = 5
    seed: int = 20260813
    include_generation: bool = False
    llm_judge: bool = False
    idempotency_key: str | None = None


@router.post("", status_code=202)
async def create_evaluation(req: EvaluationRequest, db: AsyncSession = Depends(get_db)):
    path = Path(req.dataset_path).resolve()
    if not path.is_file():
        raise HTTPException(status_code=400, detail="Dataset not found")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    key = req.idempotency_key or f"evaluation:{digest}:{req.top_k}:{req.seed}:{req.include_generation}:{req.llm_judge}"
    task, created = await TaskService(db).create_or_get("evaluation", key, "evaluation")
    if created:
        output_dir = os.path.join(settings.evaluation_output_dir, str(task.id))
        run = EvaluationRun(
            task_id=task.id,
            dataset_path=str(path),
            dataset_sha256=digest,
            parameters=req.model_dump(),
            output_dir=output_dir,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        task.resource_id = run.id
        try:
            result = run_evaluation_task.apply_async(args=[str(task.id), str(run.id)], retry=False)
            task.celery_task_id = result.id
            await db.commit()
        except Exception as exc:
            run.status = "failed"
            run.error_message = f"{type(exc).__name__}: {exc}"[:4000]
            await TaskService(db).mark_failed(task, exc)
            await db.commit()
            raise HTTPException(status_code=503, detail="Evaluation task could not be queued") from exc
    return {"task": serialize_task(task), "created": created}


@router.get("/{run_id}")
async def get_evaluation(run_id: str, db: AsyncSession = Depends(get_db)):
    try:
        run_uuid = parse_uuid(run_id)
        run = await db.get(EvaluationRun, run_uuid) if run_uuid else None
    except (ValueError, TypeError):
        run = None
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    payload = serialize_run(run)
    output_dir = Path(run.output_dir)
    payload["cases"] = []
    cases_path = output_dir / "cases.jsonl"
    if cases_path.is_file():
        import json
        payload["cases"] = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    payload["failures"] = read_json(output_dir / "failures.json", {"count": 0, "failures": []})
    payload["comparison_csv_available"] = (output_dir / "comparison.csv").is_file()
    return payload
