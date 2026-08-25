from typing import List, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.embedding_service import EmbeddingService
from app.services.retrieval_service import RetrievalService
from app.services.bm25_service import BM25Service
from app.services.hybrid_service import HybridSearchService, insufficient_evidence_answer
from app.services.rag_service import RAGService
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.conversation_document import ConversationDocument
from app.services.persistence_service import RetrievalPersistence, persist_message_with_citations
from time import perf_counter
from app.services.ids import parse_uuid

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    query: str
    conversation_id: str | None = None
    document_ids: List[str] = Field(default_factory=list)
    top_k: int = 5
    rerank: bool = True
    knowledge_mode: Literal["grounded", "hybrid", "general"] = "hybrid"


class Citation(BaseModel):
    citation_number: int
    document_title: str
    document_id: str
    section_title: str | None = None
    content_snippet: str
    chunk_id: str


class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    answer: str
    conversation_id: str
    citations: list[Citation]
    token_usage: TokenUsage
    sources_used: int
    retrieval_diagnostics: dict


class MessageItem(BaseModel):
    id: str
    role: str
    content: str
    citations: list = Field(default_factory=list)
    created_at: str


class ConversationDetail(BaseModel):
    id: str
    title: str
    document_ids: List[str]
    messages: List[MessageItem]


class CreateConversationRequest(BaseModel):
    title: str = "New Conversation"
    document_ids: List[str] = Field(default_factory=list)


@router.post("/conversations", status_code=201)
async def create_conversation(req: CreateConversationRequest, db: AsyncSession = Depends(get_db)):
    document_ids = []
    for raw_id in dict.fromkeys(req.document_ids):
        document_id = parse_uuid(raw_id)
        if not document_id:
            raise HTTPException(status_code=400, detail=f"Invalid document id: {raw_id}")
        document_ids.append(document_id)
    conv = Conversation(title=(req.title.strip() or "New Conversation")[:500])
    db.add(conv)
    await db.flush()
    for document_id in document_ids:
        db.add(ConversationDocument(conversation_id=conv.id, document_id=document_id))
    await db.commit()
    await db.refresh(conv)
    return {"id": str(conv.id), "title": conv.title, "document_ids": [str(item) for item in document_ids]}


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    retrieval = RetrievalService(db)
    bm25 = BM25Service(db)
    hybrid = HybridSearchService(retrieval, bm25)
    rag = RAGService()
    embed_service = EmbeddingService()

    # Determine document_ids: from request or from conversation's saved docs
    conversation_uuid = parse_uuid(req.conversation_id) if req.conversation_id else None
    if req.conversation_id and not conversation_uuid:
        raise HTTPException(status_code=400, detail="Invalid conversation id")
    doc_ids = []
    for raw_id in dict.fromkeys(req.document_ids):
        document_id = parse_uuid(raw_id)
        if not document_id:
            raise HTTPException(status_code=400, detail=f"Invalid document id: {raw_id}")
        doc_ids.append(str(document_id))
    if not doc_ids and req.conversation_id:
        result = await db.execute(
            select(ConversationDocument.document_id).where(
                ConversationDocument.conversation_id == conversation_uuid
            )
        )
        doc_ids = [str(r[0]) for r in result.fetchall()]

    search_results = []
    retrieval_record = None
    retrieval_started = None
    retrieval_diagnostics = {
        "explicit_document_scope": bool(doc_ids),
        "selected_document_ids": doc_ids,
        "missing_documents": [],
        "evidence_status": "not_applicable" if req.knowledge_mode == "general" else "sufficient",
        "evidence_message": None,
    }
    if req.knowledge_mode != "general":
        retrieval_record, retrieval_started = await RetrievalPersistence(db).start(
            req.query, "hybrid", doc_ids, req.top_k, conversation_id=conversation_uuid,
        )
        query_embedding = await embed_service.embed_single(req.query)
        try:
            search_results, retrieval_diagnostics = await hybrid.search_with_diagnostics(
                query=req.query, query_embedding=query_embedding, vector_top_k=30,
                bm25_top_k=30, final_top_k=20, document_ids=doc_ids,
            )
            await RetrievalPersistence(db).succeed(
                retrieval_record, retrieval_started, search_results, retrieval_diagnostics,
            )
        except Exception as exc:
            await RetrievalPersistence(db).fail(retrieval_record, retrieval_started, exc)
            raise

    if req.rerank and len(search_results) > req.top_k:
        search_results = await rag.rerank(req.query, search_results, top_k=req.top_k)
    else:
        search_results = search_results[:req.top_k]

    history = None
    conv = None
    if req.conversation_id:
        conv = await db.get(Conversation, conversation_uuid)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conv:
            result = await db.execute(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.created_at.asc())
                .limit(20)
            )
            history = [{"role": m.role, "content": m.content} for m in result.scalars().all()]

    if not conv:
        conv = Conversation(title=req.query[:80] if len(req.query) > 80 else req.query)
        db.add(conv)
        await db.commit()
        await db.refresh(conv)

    # Associate documents with conversation
    if doc_ids:
        for did in doc_ids:
            document_uuid = parse_uuid(did)
            existing = await db.execute(
                select(ConversationDocument).where(
                    ConversationDocument.conversation_id == conv.id,
                    ConversationDocument.document_id == document_uuid,
                )
            )
            if not existing.scalar():
                db.add(ConversationDocument(conversation_id=conv.id, document_id=document_uuid))
        await db.commit()

    if doc_ids and retrieval_diagnostics.get("evidence_status") == "insufficient":
        result = {
            "answer": insufficient_evidence_answer(retrieval_diagnostics),
            "citations": [],
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    else:
        result = await rag.generate(
            req.query, search_results, history,
            allow_general_knowledge=req.knowledge_mode in ("hybrid", "general"),
        )

    if retrieval_record and retrieval_record.conversation_id is None:
        retrieval_record.conversation_id = conv.id
    await persist_message_with_citations(db, conv.id, "user", req.query)
    await persist_message_with_citations(
        db, conv.id, "assistant", result["answer"], result["citations"],
        retrieval_record_id=retrieval_record.id if retrieval_record else None,
    )

    return ChatResponse(
        answer=result["answer"],
        conversation_id=str(conv.id),
        citations=[Citation(**c) for c in result["citations"]],
        token_usage=TokenUsage(**result["token_usage"]),
        sources_used=len(search_results),
        retrieval_diagnostics=retrieval_diagnostics,
    )


@router.get("/conversations", response_model=List[dict])
async def list_conversations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation).order_by(Conversation.created_at.desc()).limit(50)
    )
    convs = result.scalars().all()
    output = []
    for c in convs:
        doc_result = await db.execute(
            select(ConversationDocument.document_id).where(ConversationDocument.conversation_id == c.id)
        )
        doc_ids = [str(r[0]) for r in doc_result.fetchall()]
        output.append({"id": str(c.id), "title": c.title, "document_ids": doc_ids, "created_at": c.created_at.isoformat() if c.created_at else None})
    return output


@router.get("/conversations/{conv_id}", response_model=ConversationDetail)
async def get_conversation(conv_id: str, db: AsyncSession = Depends(get_db)):
    conv_uuid = parse_uuid(conv_id)
    conv = await db.get(Conversation, conv_uuid) if conv_uuid else None
    if not conv:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
    )
    msgs = result.scalars().all()

    doc_result = await db.execute(
        select(ConversationDocument.document_id).where(ConversationDocument.conversation_id == conv.id)
    )
    doc_ids = [str(r[0]) for r in doc_result.fetchall()]

    return ConversationDetail(
        id=str(conv.id),
        title=conv.title,
        document_ids=doc_ids,
        messages=[
            MessageItem(
                id=str(m.id), role=m.role, content=m.content,
                citations=m.citations or [], created_at=m.created_at.isoformat() if m.created_at else "",
            )
            for m in msgs
        ],
    )


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, db: AsyncSession = Depends(get_db)):
    conv_uuid = parse_uuid(conv_id)
    conv = await db.get(Conversation, conv_uuid) if conv_uuid else None
    if not conv:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.delete(conv)
    await db.commit()
    return {"detail": "deleted"}


class AddDocumentRequest(BaseModel):
    document_id: str


class RenameConversationRequest(BaseModel):
    title: str


@router.patch("/conversations/{conv_id}")
async def rename_conversation(conv_id: str, req: RenameConversationRequest, db: AsyncSession = Depends(get_db)):
    conv_uuid = parse_uuid(conv_id)
    conv = await db.get(Conversation, conv_uuid) if conv_uuid else None
    if not conv:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Conversation not found")
    title = req.title.strip()
    if not title:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="标题不能为空")
    conv.title = title[:500]
    await db.commit()
    return {"id": str(conv.id), "title": conv.title}


@router.post("/conversations/{conv_id}/documents")
async def add_document_to_conv(conv_id: str, req: AddDocumentRequest, db: AsyncSession = Depends(get_db)):
    conv_uuid = parse_uuid(conv_id)
    document_uuid = parse_uuid(req.document_id)
    conv = await db.get(Conversation, conv_uuid) if conv_uuid else None
    if not conv:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not document_uuid:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid document id")

    existing = await db.execute(
        select(ConversationDocument).where(
            ConversationDocument.conversation_id == conv.id,
            ConversationDocument.document_id == document_uuid,
        )
    )
    if existing.scalar():
        return {"detail": "already exists"}

    db.add(ConversationDocument(conversation_id=conv.id, document_id=document_uuid))
    await db.commit()
    return {"detail": "added"}


@router.delete("/conversations/{conv_id}/documents/{doc_id}")
async def remove_document_from_conv(conv_id: str, doc_id: str, db: AsyncSession = Depends(get_db)):
    conv_uuid = parse_uuid(conv_id)
    doc_uuid = parse_uuid(doc_id)
    if not conv_uuid or not doc_uuid:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid conversation or document id")
    await db.execute(
        delete(ConversationDocument).where(
            ConversationDocument.conversation_id == conv_uuid,
            ConversationDocument.document_id == doc_uuid,
        )
    )
    await db.commit()
    return {"detail": "removed"}
