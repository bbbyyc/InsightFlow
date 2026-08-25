from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.task_service import TaskService, serialize_task

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/{task_id}")
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)):
    record = await TaskService(db).get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    return serialize_task(record)
