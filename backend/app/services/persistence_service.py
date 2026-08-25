from datetime import datetime, timezone
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.agent_run import AgentNodeEvent, AgentRun
from app.models.citation import Citation
from app.models.message import Message
from app.models.retrieval_record import RetrievalRecord
from app.services.ids import parse_uuid


def utcnow():
    return datetime.now(timezone.utc)


class RetrievalPersistence:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def start(self, query: str, mode: str, document_ids: list[str], top_k: int, conversation_id=None):
        record = RetrievalRecord(
            conversation_id=parse_uuid(conversation_id),
            query=query,
            mode=mode,
            document_ids=document_ids,
            top_k=top_k,
            status="running",
        )
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return record, perf_counter()

    async def succeed(self, record: RetrievalRecord, started: float, results: list[dict], diagnostics: dict, rewritten_query=None):
        record.status = "completed"
        record.rewritten_query = rewritten_query
        record.result_chunk_ids = [str(item["chunk_id"]) for item in results]
        record.diagnostics = diagnostics
        record.latency_ms = (perf_counter() - started) * 1000
        record.completed_at = utcnow()
        await self.db.commit()

    async def fail(self, record: RetrievalRecord, started: float, exc: Exception):
        record.status = "failed"
        record.error_message = f"{type(exc).__name__}: {exc}"[:4000]
        record.latency_ms = (perf_counter() - started) * 1000
        record.completed_at = utcnow()
        await self.db.commit()


async def persist_message_with_citations(
    db: AsyncSession,
    conversation_id,
    role: str,
    content: str,
    citations: list[dict] | None = None,
    retrieval_record_id=None,
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        citations=citations or [],
        retrieval_record_id=retrieval_record_id,
    )
    db.add(message)
    await db.flush()
    for item in citations or []:
        try:
            chunk_id = UUID(str(item["chunk_id"])) if item.get("chunk_id") else None
            document_id = UUID(str(item["document_id"])) if item.get("document_id") else None
        except ValueError:
            chunk_id = document_id = None
        db.add(Citation(
            message_id=message.id,
            retrieval_record_id=retrieval_record_id,
            chunk_id=chunk_id,
            document_id=document_id,
            citation_number=item["citation_number"],
            document_title=item["document_title"],
            section_title=item.get("section_title"),
            content_snippet=item["content_snippet"],
        ))
    await db.commit()
    await db.refresh(message)
    return message


class AgentRunRecorder:
    def __init__(self, db: AsyncSession, run: AgentRun):
        self.db = db
        self.run = run
        self.sequence = 0

    async def _current_status(self) -> str | None:
        return await self.db.scalar(select(AgentRun.status).where(AgentRun.id == self.run.id))

    @classmethod
    async def create(cls, db: AsyncSession, query: str, document_ids: list[str], conversation_id=None):
        run = AgentRun(
            query=query,
            document_ids=document_ids,
            conversation_id=parse_uuid(conversation_id),
            status="running",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return cls(db, run)

    async def event(self, node: str, status: str, duration_ms=None, input_summary=None, result_summary=None, error=None):
        if await self._current_status() == "cancelled":
            return
        self.sequence += 1
        self.run.current_node = node
        self.db.add(AgentNodeEvent(
            agent_run_id=self.run.id,
            sequence=self.sequence,
            node=node,
            status=status,
            duration_ms=duration_ms,
            input_summary=input_summary or {},
            result_summary=result_summary or {},
            error_message=str(error)[:4000] if error else None,
        ))
        await self.db.commit()

    async def complete(self, answer: str):
        if await self._current_status() == "cancelled":
            return
        self.run.status = "completed"
        self.run.answer_summary = answer[:1000]
        self.run.completed_at = utcnow()
        await self.db.commit()

    async def fail(self, exc: BaseException, status="failed"):
        if await self._current_status() == "cancelled" and status != "cancelled":
            return
        self.run.status = status
        self.run.error_message = f"{type(exc).__name__}: {exc}"[:4000]
        self.run.completed_at = utcnow()
        await self.db.commit()
