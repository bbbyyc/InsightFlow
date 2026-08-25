from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
import json
from pydantic import BaseModel, Field

from app.services.agent_service import InsightFlowAgent
from app.database import get_db
from app.services.persistence_service import AgentRunRecorder
from app.services.persistence_service import persist_message_with_citations
from app.services.ids import parse_uuid
from app.models.conversation import Conversation
from app.models.agent_run import AgentRun

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/runs/{run_id}/cancel")
async def cancel_agent_run(run_id: str, db: AsyncSession = Depends(get_db)):
    from fastapi import HTTPException
    run_uuid = parse_uuid(run_id)
    run = await db.get(AgentRun, run_uuid) if run_uuid else None
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")
    if run.status in {"completed", "failed", "cancelled"}:
        return {"id": str(run.id), "status": run.status}
    run.status = "cancelled"
    run.error_message = "Cancelled by client"
    from app.services.persistence_service import utcnow
    run.completed_at = utcnow()
    await db.commit()
    return {"id": str(run.id), "status": "cancelled"}


class AgentRequest(BaseModel):
    query: str
    max_iterations: int = 2
    document_ids: list[str] = Field(default_factory=list)
    conversation_id: str | None = None


class AgentResponse(BaseModel):
    answer: str
    citations: list[dict]
    iterations: int
    search_queries: list[str]
    token_usage: dict
    retrieval_diagnostics: dict
    retrieval_results: list[dict]
    conversation_id: str


async def _conversation_for_request(db: AsyncSession, req: AgentRequest) -> Conversation:
    if req.conversation_id:
        conversation_id = parse_uuid(req.conversation_id)
        conversation = await db.get(Conversation, conversation_id) if conversation_id else None
        if not conversation:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.title in {"New Conversation", "新会话"}:
            from app.models.message import Message
            from sqlalchemy import select
            first_query = await db.scalar(
                select(Message.content)
                .where(Message.conversation_id == conversation.id, Message.role == "user")
                .order_by(Message.created_at.asc())
                .limit(1)
            )
            conversation.title = (first_query or req.query.strip() or "新会话")[:80]
            await db.commit()
        return conversation
    conversation = Conversation(title=(req.query.strip() or "New Conversation")[:80])
    db.add(conversation)
    await db.flush()
    if req.document_ids:
        from app.models.conversation_document import ConversationDocument
        from app.services.ids import require_uuid
        for raw_id in dict.fromkeys(req.document_ids):
            db.add(ConversationDocument(
                conversation_id=conversation.id,
                document_id=require_uuid(raw_id),
            ))
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.post("", response_model=AgentResponse)
async def run_agent(req: AgentRequest, db: AsyncSession = Depends(get_db)):
    conversation = await _conversation_for_request(db, req)
    await persist_message_with_citations(db, conversation.id, "user", req.query)
    recorder = await AgentRunRecorder.create(db, req.query, req.document_ids, conversation.id)

    async def record(event):
        if event["event"] in {"plan", "search", "rewrite", "generate"}:
            await recorder.event(
                event["event"], event["status"], duration_ms=event.get("duration_ms"),
                result_summary=event.get("summary", {}),
            )

    try:
        result = await InsightFlowAgent(event_callback=record, conversation_id=conversation.id).run(
            req.query, req.max_iterations, req.document_ids,
        )
        await recorder.complete(result.get("answer", ""))
        await persist_message_with_citations(
            db, conversation.id, "assistant", result.get("answer", ""), result.get("citations", []),
        )
        return AgentResponse(**result, conversation_id=str(conversation.id))
    except Exception as exc:
        await recorder.fail(exc)
        raise


@router.post("/stream")
async def stream_agent(req: AgentRequest, request: Request, db: AsyncSession = Depends(get_db)):
    conversation = await _conversation_for_request(db, req)
    await persist_message_with_citations(db, conversation.id, "user", req.query)
    recorder = await AgentRunRecorder.create(db, req.query, req.document_ids, conversation.id)

    async def record(event):
        if event["event"] in {"plan", "search", "rewrite", "generate"}:
            await recorder.event(
                event["event"], event["status"], duration_ms=event.get("duration_ms"),
                result_summary=event.get("summary", {}),
            )

    agent = InsightFlowAgent(event_callback=record, conversation_id=conversation.id)

    async def events():
        terminal = False
        try:
            yield f"event: accepted\ndata: {json.dumps({'run_id': str(recorder.run.id), 'conversation_id': str(conversation.id)}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0)
            if await request.is_disconnected():
                await recorder.fail(asyncio.CancelledError("client disconnected"), status="cancelled")
                terminal = True
                return
            # Give the client a real cancellation window after the accepted event.
            await asyncio.sleep(0.15)
            if await request.is_disconnected():
                await recorder.fail(asyncio.CancelledError("client disconnected"), status="cancelled")
                terminal = True
                return
            async for event in agent.stream(req.query, req.max_iterations, req.document_ids):
                if await request.is_disconnected():
                    await recorder.fail(asyncio.CancelledError("client disconnected"), status="cancelled")
                    break
                if event["event"] == "complete":
                    await recorder.complete(event["result"].get("answer", ""))
                    await persist_message_with_citations(
                        db, conversation.id, "assistant", event["result"].get("answer", ""),
                        event["result"].get("citations", []),
                    )
                    terminal = True
                elif event["event"] == "failed":
                    await recorder.fail(RuntimeError(event.get("error", "failed")), status=event.get("status", "failed"))
                    terminal = True
                yield f"event: {event['event']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError as exc:
            await db.rollback()
            await recorder.fail(exc, status="cancelled")
            terminal = True
            raise
        except Exception as exc:
            await recorder.fail(exc)
            terminal = True
            payload = {"event": "failed", "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            yield f"event: failed\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            if not terminal:
                await db.rollback()
                await recorder.fail(asyncio.CancelledError("stream interrupted"), status="cancelled")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Agent-Run-Id": str(recorder.run.id),
            "X-Conversation-Id": str(conversation.id),
        },
    )
