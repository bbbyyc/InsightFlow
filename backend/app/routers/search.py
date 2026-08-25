from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal

from app.database import get_db
from app.services.embedding_service import EmbeddingService
from app.services.retrieval_service import RetrievalService
from app.services.bm25_service import BM25Service
from app.services.hybrid_service import (
    MIN_VECTOR_EVIDENCE_SCORE,
    HybridSearchService,
    annotate_single_channel,
    build_scope_diagnostics,
    recall_quota,
)

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10
    threshold: float = 0.0
    mode: Literal["vector", "bm25", "hybrid"] = "hybrid"
    document_ids: list[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    chunk_id: str
    content: str
    document_id: str
    document_title: str
    document_type: str
    chunk_index: int
    page_number: int | None = None
    section_title: str | None = None
    score: float
    retrieval_channels: list[str]
    channel_ranks: dict[str, int]
    channel_scores: dict[str, float]
    rrf_rank: int
    fused_rank: int


class SearchResponse(BaseModel):
    results: list[SearchResult]
    query: str
    mode: str
    total: int
    diagnostics: dict


@router.post("", response_model=SearchResponse)
async def search(req: SearchRequest, db: AsyncSession = Depends(get_db)):
    retrieval = RetrievalService(db)
    bm25 = BM25Service(db)

    if req.mode == "bm25":
        document_ids = req.document_ids or None
        results = await bm25.search(
            req.query,
            req.top_k,
            document_ids=document_ids,
            per_document_limit=recall_quota(req.top_k, len(req.document_ids)),
        )
        raw_items = [r.__dict__ for r in results]
        annotated = annotate_single_channel(raw_items, "bm25")
        items = [SearchResult(**item) for item in annotated]
        diagnostics = build_scope_diagnostics(req.document_ids, annotated)
        return SearchResponse(results=items, query=req.query, mode=req.mode, total=len(items), diagnostics=diagnostics)

    embed_service = EmbeddingService()
    query_embedding = await embed_service.embed_single(req.query)

    if req.mode == "vector":
        document_ids = req.document_ids or None
        effective_threshold = max(req.threshold, MIN_VECTOR_EVIDENCE_SCORE) if document_ids else req.threshold
        results = await retrieval.vector_search(
            query_embedding,
            req.top_k,
            effective_threshold,
            document_ids=document_ids,
            per_document_limit=recall_quota(req.top_k, len(req.document_ids)),
        )
        annotated = annotate_single_channel(results, "vector")
        return SearchResponse(
            results=[SearchResult(**r) for r in annotated],
            query=req.query,
            mode=req.mode,
            total=len(annotated),
            diagnostics=build_scope_diagnostics(req.document_ids, annotated),
        )

    hybrid = HybridSearchService(retrieval, bm25)
    results, diagnostics = await hybrid.search_with_diagnostics(
        query=req.query,
        query_embedding=query_embedding,
        vector_top_k=max(req.top_k * 2, 20),
        bm25_top_k=max(req.top_k * 2, 20),
        final_top_k=req.top_k,
        threshold=req.threshold,
        document_ids=req.document_ids or None,
    )
    return SearchResponse(
        results=[SearchResult(**r) for r in results],
        query=req.query,
        mode=req.mode,
        total=len(results),
        diagnostics=diagnostics,
    )


@router.post("/rebuild-index")
async def rebuild_index(db: AsyncSession = Depends(get_db)):
    bm25 = BM25Service(db)
    await bm25.rebuild_index()
    return {"status": "ok", "message": "BM25 index rebuilt"}
