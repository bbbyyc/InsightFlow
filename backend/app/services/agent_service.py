from typing import TypedDict, Annotated, List, AsyncIterator, Callable, Awaitable
import operator
import httpx
import asyncio
from time import perf_counter

from langgraph.graph import StateGraph, END
from app.config import settings


class AgentState(TypedDict):
    query: str
    search_queries: Annotated[List[str], operator.add]
    search_results: Annotated[List[dict], operator.add]
    document_ids: List[str]
    iteration: int
    max_iterations: int
    answer: str
    citations: List[dict]
    token_usage: dict
    retrieval_diagnostics: dict


class InsightFlowAgent:

    def __init__(self, event_callback: Callable[[dict], Awaitable[None]] | None = None, conversation_id=None):
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url
        self.model = settings.deepseek_model
        self.graph = self._build_graph()
        self.event_callback = event_callback
        self.conversation_id = conversation_id

    async def _emit(self, event: str, status: str, summary: dict | None = None, duration_ms: float | None = None):
        if self.event_callback:
            await self.event_callback({"event": event, "status": status, "summary": summary or {}, "duration_ms": duration_ms})

    def _build_graph(self):
        builder = StateGraph(AgentState)

        builder.add_node("plan", self._plan)
        builder.add_node("search", self._search)
        builder.add_node("generate", self._generate)

        builder.set_entry_point("plan")
        builder.add_edge("plan", "search")
        builder.add_conditional_edges(
            "search",
            self._should_continue,
            {"continue": "plan", "generate": "generate"},
        )
        builder.add_edge("generate", END)

        return builder.compile()

    async def _plan(self, state: AgentState) -> dict:
        query = state["query"]
        iteration = state.get("iteration", 0)

        if iteration == 0:
            started = perf_counter()
            await self._emit("plan", "started", {"query_length": len(query)})
            result = {"search_queries": [query], "iteration": 1}
            await self._emit("plan", "completed", {"search_query_count": 1}, (perf_counter() - started) * 1000)
            return result

        started = perf_counter()
        await self._emit("rewrite", "started", {"previous_query_count": len(state.get("search_queries", []))})

        prompt = f"""Based on the original question and previous search results, generate ONE refined search query.

Original: {query}
Previous searches: {', '.join(state.get('search_queries', []))}

Return only the refined query, nothing else."""

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "max_tokens": 100},
            )
            resp.raise_for_status()
            refined = resp.json()["choices"][0]["message"]["content"].strip()

        result = {"search_queries": [refined], "iteration": iteration + 1}
        await self._emit("rewrite", "completed", {"rewritten_query_length": len(refined)}, (perf_counter() - started) * 1000)
        return result

    async def _search(self, state: AgentState) -> dict:
        from app.database import async_session
        from app.services.embedding_service import EmbeddingService
        from app.services.retrieval_service import RetrievalService
        from app.services.bm25_service import BM25Service
        from app.services.hybrid_service import HybridSearchService

        latest_query = state["search_queries"][-1] if state["search_queries"] else state["query"]
        started = perf_counter()
        await self._emit("search", "started", {"iteration": state.get("iteration", 0)})

        async with async_session() as db:
            retrieval = RetrievalService(db)
            bm25 = BM25Service(db)
            hybrid = HybridSearchService(retrieval, bm25)
            embed_svc = EmbeddingService()
            from app.services.persistence_service import RetrievalPersistence
            persistence = RetrievalPersistence(db)
            record, retrieval_started = await persistence.start(
                latest_query, "hybrid", state.get("document_ids") or [], 5,
                conversation_id=self.conversation_id,
            )

            try:
                query_emb = await embed_svc.embed_single(latest_query)
                results, diagnostics = await hybrid.search_with_diagnostics(
                    query=latest_query, query_embedding=query_emb,
                    vector_top_k=10, bm25_top_k=10, final_top_k=5,
                    document_ids=state.get("document_ids") or None,
                )
                await persistence.succeed(record, retrieval_started, results, diagnostics, rewritten_query=latest_query)
            except Exception as exc:
                await persistence.fail(record, retrieval_started, exc)
                raise

        await self._emit("search", "completed", {
            "result_count": len(results), "evidence_status": diagnostics.get("evidence_status"),
        }, (perf_counter() - started) * 1000)
        return {"search_results": results, "retrieval_diagnostics": diagnostics}

    def _should_continue(self, state: AgentState) -> str:
        if state.get("iteration", 0) >= state.get("max_iterations", 3):
            return "generate"
        if len(state.get("search_results", [])) == 0:
            return "generate"
        return "continue"

    async def _generate(self, state: AgentState) -> dict:
        from app.services.rag_service import RAGService
        from app.services.hybrid_service import insufficient_evidence_answer

        started = perf_counter()
        await self._emit("generate", "started", {"context_count": len(state.get("search_results", []))})
        diagnostics = state.get("retrieval_diagnostics", {})
        if state.get("document_ids") and diagnostics.get("evidence_status") == "insufficient":
            result = {
                "answer": insufficient_evidence_answer(diagnostics),
                "citations": [],
                "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            await self._emit("generate", "completed", {"answer_length": len(result["answer"]), "model_called": False}, (perf_counter() - started) * 1000)
            return result

        rag = RAGService()

        # Multiple search rounds commonly recall the same chunk. Always dedupe
        # before numbering context so [N] remains stable and cannot point at a
        # duplicate copy of the same evidence.
        unique = {}
        for chunk in state.get("search_results", []):
            chunk_id = str(chunk.get("chunk_id", ""))
            if chunk_id and chunk_id not in unique:
                unique[chunk_id] = chunk
        all_chunks = list(unique.values())

        search_info = "\n".join(
            f"Search #{i+1}: {q}" for i, q in enumerate(state.get("search_queries", [state["query"]]))
        )
        query = f"{state['query']}\n\n[Agent searched: {search_info}]"

        async def stream_token(delta: str):
            await self._emit("answer_delta", "streaming", {"delta": delta})

        result = await rag.generate(
            query, all_chunks[:8], allow_general_knowledge=False,
            token_callback=stream_token,
        )

        output = {
            "answer": result["answer"],
            "citations": result["citations"],
            "token_usage": result["token_usage"],
        }
        await self._emit("generate", "completed", {"answer_length": len(output["answer"]), "citation_count": len(output["citations"]), "model_called": True}, (perf_counter() - started) * 1000)
        return output

    async def run(self, query: str, max_iterations: int = 2, document_ids: List[str] | None = None) -> dict:
        initial_state: AgentState = {
            "query": query,
            "search_queries": [],
            "search_results": [],
            "document_ids": document_ids or [],
            "iteration": 0,
            "max_iterations": max_iterations,
            "answer": "",
            "citations": [],
            "token_usage": {},
            "retrieval_diagnostics": {},
        }
        result = await self.graph.ainvoke(initial_state)
        return {
            "answer": result.get("answer", ""),
            "citations": result.get("citations", []),
            "token_usage": result.get("token_usage", {}),
            "iterations": result.get("iteration", 1),
            "search_queries": result.get("search_queries", []),
            "retrieval_diagnostics": result.get("retrieval_diagnostics", {}),
            "retrieval_results": result.get("search_results", [])[:20],
        }

    async def stream(self, query: str, max_iterations: int = 2, document_ids: List[str] | None = None) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        external_callback = self.event_callback

        async def queue_callback(event):
            if external_callback:
                await external_callback(event)
            await queue.put(event)

        self.event_callback = queue_callback

        async def execute():
            try:
                result = await self.run(query, max_iterations, document_ids)
                await queue.put({"event": "complete", "status": "completed", "result": result})
            except asyncio.CancelledError:
                await queue.put({"event": "failed", "status": "cancelled", "error": "client_disconnected"})
                raise
            except Exception as exc:
                await queue.put({"event": "failed", "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            finally:
                await queue.put(None)

        task = asyncio.create_task(execute())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
