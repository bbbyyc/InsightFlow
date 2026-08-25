import os
import hashlib
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import settings
from app.services.document_service import DocumentService
from app.tasks import process_document_task
from app.services.task_service import TaskService, serialize_task
from app.models.document import DocumentStatus
from app.models.task_record import TaskRecord

router = APIRouter(prefix="/api/documents", tags=["documents"])


async def latest_task(db: AsyncSession, document_id):
    return await db.scalar(
        select(TaskRecord)
        .where(TaskRecord.resource_type == "document", TaskRecord.resource_id == document_id)
        .order_by(TaskRecord.created_at.desc(), TaskRecord.id.desc())
        .limit(1)
    )


def document_payload(document, task=None, include_chunks=False, chunks=None):
    payload = {
        "id": str(document.id), "title": document.title, "file_type": document.file_type,
        "status": document.status.value if hasattr(document.status, "value") else str(document.status),
        "chunk_count": document.chunk_count or 0, "error_message": document.error_message,
        "processed_at": document.processed_at.isoformat() if document.processed_at else None,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "task": serialize_task(task) if task else None,
    }
    if include_chunks:
        payload["chunks"] = [{
            "id": str(c.id), "content": c.content, "chunk_index": c.chunk_index,
            "page_number": c.page_number, "section_title": c.section_title,
            "token_count": c.token_count,
        } for c in (chunks or [])]
    return payload


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    allowed_types = {"pdf", "md", "markdown", "txt"}
    file_type = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if file_type not in allowed_types:
        raise HTTPException(status_code=415, detail="仅支持 PDF、Markdown 和 TXT 文件")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件不能超过 {settings.max_upload_size_mb}MB")

    content_sha256 = hashlib.sha256(content).hexdigest()
    task_key = idempotency_key or f"document:{content_sha256}:{file.filename.strip().lower()}"
    task_service = TaskService(db)
    task, created = await task_service.create_or_get(
        "document_processing", task_key, "document",
    )
    if not created:
        if not task.resource_id:
            raise HTTPException(status_code=409, detail="The original upload is still being initialized")
        existing_doc = await DocumentService(db).get_document(task.resource_id)
        if not existing_doc:
            raise HTTPException(status_code=409, detail="The original upload no longer exists")
        return {
            "id": str(existing_doc.id), "title": existing_doc.title,
            "status": existing_doc.status.value if hasattr(existing_doc.status, "value") else str(existing_doc.status),
            "chunk_count": existing_doc.chunk_count, "error_message": existing_doc.error_message,
            "task": serialize_task(task), "created": False,
        }

    service = DocumentService(db)
    doc = await service.create_document(file.filename, file_type)
    doc.content_sha256 = content_sha256
    task.resource_id = doc.id
    await db.commit()

    os.makedirs(settings.upload_dir, exist_ok=True)
    with open(doc.file_path, "wb") as f:
        f.write(content)

    try:
        result = process_document_task.apply_async(args=[str(task.id), str(doc.id)], retry=False)
        task.celery_task_id = result.id
        await db.commit()
    except Exception as exc:
        doc.status = DocumentStatus.FAILED
        doc.error_message = f"{type(exc).__name__}: {exc}"[:4000]
        await task_service.mark_failed(task, exc)
        await db.commit()
        raise HTTPException(status_code=503, detail="Document task could not be queued") from exc

    return {"id": str(doc.id), "title": doc.title, "status": doc.status.value, "chunk_count": doc.chunk_count, "error_message": doc.error_message, "task": serialize_task(task), "created": True}


@router.get("")
async def list_documents(db: AsyncSession = Depends(get_db)):
    service = DocumentService(db)
    docs = await service.list_documents()
    return [document_payload(d, await latest_task(db, d.id)) for d in docs]


@router.get("/{document_id}")
async def get_document(document_id: str, db: AsyncSession = Depends(get_db)):
    service = DocumentService(db)
    doc = await service.get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    chunks = await service.get_chunks(document_id)
    return document_payload(doc, await latest_task(db, doc.id), include_chunks=True, chunks=chunks)


@router.post("/{document_id}/retry", status_code=202)
async def retry_document(document_id: str, db: AsyncSession = Depends(get_db)):
    service = DocumentService(db)
    doc = await service.get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status not in {DocumentStatus.FAILED, DocumentStatus.PENDING}:
        raise HTTPException(status_code=409, detail="Only failed or pending documents can be retried")
    task_service = TaskService(db)
    task, _ = await task_service.create_or_get(
        "document_processing", f"document-retry:{doc.id}:{os.urandom(8).hex()}", "document", doc.id,
    )
    doc.status = DocumentStatus.PENDING
    doc.error_message = None
    await db.commit()
    try:
        result = process_document_task.apply_async(args=[str(task.id), str(doc.id)], retry=False)
        task.celery_task_id = result.id
        await db.commit()
    except Exception as exc:
        doc.status = DocumentStatus.FAILED
        doc.error_message = f"{type(exc).__name__}: {exc}"[:4000]
        await task_service.mark_failed(task, exc)
        raise HTTPException(status_code=503, detail="Document task could not be queued") from exc
    return {"document": document_payload(doc, task), "task": serialize_task(task)}


@router.delete("/{document_id}")
async def delete_document(document_id: str, db: AsyncSession = Depends(get_db)):
    service = DocumentService(db)
    doc = await service.get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    await service.delete_document(document_id)
    return {"detail": "deleted"}
