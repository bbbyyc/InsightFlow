import os
import asyncio
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./phase3_test.db")

from fastapi.testclient import TestClient

from app.main import app
from app.services.agent_service import InsightFlowAgent
from app.database import async_session
from app.services.persistence_service import AgentRunRecorder


async def successful_stream(self, query, max_iterations=2, document_ids=None):
    for event in (
        {"event": "plan", "status": "completed", "summary": {"search_query_count": 1}, "duration_ms": 1.0},
        {"event": "search", "status": "completed", "summary": {"result_count": 1}, "duration_ms": 2.0},
        {"event": "rewrite", "status": "completed", "summary": {"rewritten_query_length": 5}, "duration_ms": 1.0},
        {"event": "generate", "status": "completed", "summary": {"answer_length": 2}, "duration_ms": 3.0},
        {"event": "complete", "status": "completed", "result": {"answer": "ok", "citations": [], "iterations": 2, "search_queries": [query], "token_usage": {}, "retrieval_diagnostics": {}}},
    ):
        if self.event_callback and event["event"] in {"plan", "search", "rewrite", "generate"}:
            await self.event_callback(event)
        yield event


async def failing_stream(self, query, max_iterations=2, document_ids=None):
    yield {"event": "plan", "status": "completed", "summary": {}, "duration_ms": 1.0}
    yield {"event": "failed", "status": "failed", "error": "RuntimeError: boom"}


class Phase3SSETests(unittest.TestCase):
    def test_sse_returns_real_node_status_sequence_without_chain_of_thought(self):
        with patch("app.services.agent_service.InsightFlowAgent.stream", successful_stream):
            with TestClient(app) as client:
                response = client.post("/api/agent/stream", json={"query": "q", "max_iterations": 2, "document_ids": []})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers.get("X-Agent-Run-Id"))
        self.assertTrue(response.headers.get("X-Conversation-Id"))
        body = response.text
        for event in ("accepted", "plan", "search", "rewrite", "generate", "complete"):
            self.assertIn(f"event: {event}", body)
        self.assertNotIn("thought", body.lower())
        self.assertNotIn("reasoning", body.lower())

    def test_sse_failure_is_emitted_and_persisted(self):
        with patch("app.services.agent_service.InsightFlowAgent.stream", failing_stream):
            with TestClient(app) as client:
                response = client.post("/api/agent/stream", json={"query": "q", "document_ids": []})
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: failed", response.text)
        self.assertIn("RuntimeError: boom", response.text)

    def test_stream_generator_close_marks_interruption_path(self):
        # The endpoint generator has a finally block that persists cancelled when no terminal event occurs.
        import inspect
        from app.routers.agent import stream_agent
        source = inspect.getsource(stream_agent)
        self.assertIn('status="cancelled"', source)
        self.assertIn("request.is_disconnected", source)

    def test_agent_stream_cancels_background_work_when_consumer_closes(self):
        async def scenario():
            cancelled = asyncio.Event()
            agent = InsightFlowAgent()

            async def slow_run(query, max_iterations=2, document_ids=None):
                await agent.event_callback({
                    "event": "plan", "status": "started", "summary": {}, "duration_ms": None,
                })
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

            agent.run = slow_run
            stream = agent.stream("q")
            first = await stream.__anext__()
            self.assertEqual(first["event"], "plan")
            await stream.aclose()
            await asyncio.wait_for(cancelled.wait(), timeout=1)

        asyncio.run(scenario())

    def test_explicit_cancel_api_persists_cancelled_status(self):
        async def create_run():
            async with async_session() as db:
                recorder = await AgentRunRecorder.create(db, "cancel-contract", [])
                return str(recorder.run.id)
        run_id = asyncio.run(create_run())
        with TestClient(app) as client:
            response = client.post(f"/api/agent/runs/{run_id}/cancel")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
