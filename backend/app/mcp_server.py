from mcp.server.fastmcp import FastMCP

mcp = FastMCP("InsightFlow")


@mcp.tool()
async def search_docs(query: str, top_k: int = 5) -> str:
    """Search the InsightFlow knowledge base using hybrid (vector + BM25 + RRF) retrieval.

    Args:
        query: The search query
        top_k: Number of results to return (default 5)

    Returns:
        JSON string with search results including scores, document names, and content snippets.
    """
    from app.database import async_session
    from app.services.embedding_service import EmbeddingService
    from app.services.retrieval_service import RetrievalService
    from app.services.bm25_service import BM25Service
    from app.services.hybrid_service import HybridSearchService
    import json

    async with async_session() as db:
        retrieval = RetrievalService(db)
        bm25 = BM25Service(db)
        hybrid = HybridSearchService(retrieval, bm25)
        embed_svc = EmbeddingService()

        query_embedding = await embed_svc.embed_single(query)
        results = await hybrid.search(
            query=query,
            query_embedding=query_embedding,
            vector_top_k=15,
            bm25_top_k=15,
            final_top_k=top_k,
        )

    output = []
    for i, r in enumerate(results):
        output.append({
            "rank": i + 1,
            "fused_rank": r.get("fused_rank", i + 1),
            "score": r["score"],
            "chunk_id": r.get("chunk_id"),
            "document_id": r.get("document_id"),
            "document": r.get("document_title", "unknown"),
            "section": r.get("section_title"),
            "retrieval_channels": r.get("retrieval_channels", []),
            "channel_ranks": r.get("channel_ranks", {}),
            "content_snippet": r["content"][:300],
        })

    return json.dumps(output, ensure_ascii=False, indent=2)


@mcp.tool()
async def ask_question(question: str) -> str:
    """Ask a question and get an AI-generated answer backed by the knowledge base with citations.

    Args:
        question: The question to ask about the knowledge base content

    Returns:
        AI-generated answer with numbered citations to source documents.
    """
    from app.database import async_session
    from app.services.embedding_service import EmbeddingService
    from app.services.retrieval_service import RetrievalService
    from app.services.bm25_service import BM25Service
    from app.services.hybrid_service import HybridSearchService
    from app.services.rag_service import RAGService

    async with async_session() as db:
        retrieval = RetrievalService(db)
        bm25 = BM25Service(db)
        hybrid = HybridSearchService(retrieval, bm25)
        rag = RAGService()
        embed_svc = EmbeddingService()

        query_embedding = await embed_svc.embed_single(question)
        search_results = await hybrid.search(
            query=question,
            query_embedding=query_embedding,
            vector_top_k=15,
            bm25_top_k=15,
            final_top_k=10,
        )
        search_results = await rag.rerank(question, search_results, top_k=5)

    result = await rag.generate(question, search_results)

    citations = "\n".join(
        f"  [{c['citation_number']}] {c['document_title']} - {c.get('section_title', 'N/A')}"
        for c in result["citations"]
    )

    return f"{result['answer']}\n\n--- Sources ---\n{citations}"


@mcp.tool()
async def list_documents() -> str:
    """List all documents currently in the InsightFlow knowledge base.

    Returns:
        JSON string with document information including titles, types, chunk counts, and status.
    """
    from app.database import async_session
    from app.services.document_service import DocumentService
    import json

    async with async_session() as db:
        svc = DocumentService(db)
        docs = await svc.list_documents()

    output = [{
        "id": str(d.id),
        "title": d.title,
        "type": d.file_type,
        "status": d.status.value if d.status else "unknown",
        "chunks": d.chunk_count,
        "created": d.created_at.isoformat() if d.created_at else None,
    } for d in docs]

    return json.dumps(output, ensure_ascii=False, indent=2)
