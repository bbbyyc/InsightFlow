import asyncio
import hashlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./phase3_test.db")
os.environ.setdefault("UPLOAD_DIR", "./phase3_test_uploads")

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import async_session
from app.main import app
from app.models import (
    Chunk, Citation, Conversation, Document, DocumentStatus, Message,
    RetrievalRecord, TaskRecord, TaskStatus,
)
from app.parsers.base import ChunkData
from app.services.document_service import DocumentService
from app.services.persistence_service import AgentRunRecorder, persist_message_with_citations
from app.services.task_service import TaskService


class Phase3PersistenceTests(unittest.TestCase):
    def test_conversation_history_persists_across_requests(self):
        with TestClient(app) as client:
            created = client.post("/api/chat/conversations", json={"title": "Persistent"})
            self.assertEqual(created.status_code, 201)
            conversation_id = created.json()["id"]
            listed = client.get("/api/chat/conversations")
            self.assertEqual(listed.status_code, 200)
            self.assertIn(conversation_id, {item["id"] for item in listed.json()})
            detail = client.get(f"/api/chat/conversations/{conversation_id}")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["title"], "Persistent")

            generated = {
                "answer": "continued", "citations": [],
                "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            with patch("app.services.rag_service.RAGService.generate", new=AsyncMock(return_value=generated)):
                continued = client.post("/api/chat", json={
                    "query": "continue", "conversation_id": conversation_id,
                    "knowledge_mode": "general", "rerank": False,
                })
            self.assertEqual(continued.status_code, 200)
            self.assertEqual(continued.json()["conversation_id"], conversation_id)
            detail = client.get(f"/api/chat/conversations/{conversation_id}")
            self.assertEqual([item["content"] for item in detail.json()["messages"]], ["continue", "continued"])

    def test_upload_query_task_and_delete_api_use_database_state(self):
        celery_id = f"celery-{uuid.uuid4()}"
        idem_key = f"upload-{uuid.uuid4()}"
        with patch("app.routers.documents.process_document_task.apply_async", return_value=SimpleNamespace(id=celery_id)) as queued:
            with TestClient(app) as client:
                upload_args = {
                    "files": {"file": ("phase3.md", b"# Test\ncontent", "text/markdown")},
                    "headers": {"Idempotency-Key": idem_key},
                }
                uploaded = client.post("/api/documents/upload", **upload_args)
                self.assertEqual(uploaded.status_code, 200)
                payload = uploaded.json()
                self.assertTrue(payload["created"])
                task_id = payload["task"]["id"]
                document_id = payload["id"]
                self.assertEqual(payload["task"]["status"], "pending")

                duplicate = client.post("/api/documents/upload", **upload_args)
                self.assertEqual(duplicate.status_code, 200)
                self.assertFalse(duplicate.json()["created"])
                self.assertEqual(duplicate.json()["id"], document_id)
                self.assertEqual(duplicate.json()["task"]["id"], task_id)

                persisted_task = client.get(f"/api/tasks/{task_id}")
                self.assertEqual(persisted_task.status_code, 200)
                self.assertEqual(persisted_task.json()["celery_task_id"], celery_id)
                self.assertEqual(client.get(f"/api/documents/{document_id}").status_code, 200)
                self.assertEqual(client.delete(f"/api/documents/{document_id}").status_code, 200)
                self.assertEqual(client.get(f"/api/documents/{document_id}").status_code, 404)
                self.assertFalse(queued.call_args.kwargs["retry"])

    def test_upload_broker_failure_is_not_left_pending(self):
        idem_key = f"broker-failure-{uuid.uuid4()}"
        with patch(
            "app.routers.documents.process_document_task.apply_async",
            side_effect=ConnectionError("redis unavailable"),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/documents/upload",
                    files={"file": ("failure.md", b"failure", "text/markdown")},
                    headers={"Idempotency-Key": idem_key},
                )
                self.assertEqual(response.status_code, 503)

        async def state():
            async with async_session() as db:
                task = await TaskService(db).get_by_idempotency_key(idem_key)
                document = await db.get(Document, task.resource_id)
                return task.status, task.error_type, document.status, document.error_message
        task_status, error_type, document_status, error_message = asyncio.run(state())
        self.assertEqual(task_status, TaskStatus.FAILED)
        self.assertEqual(error_type, "ConnectionError")
        self.assertEqual(document_status, DocumentStatus.FAILED)
        self.assertIn("redis unavailable", error_message)

    def test_chunk_retry_is_idempotent(self):
        async def scenario():
            async with async_session() as db:
                document = Document(title="idempotent.md", file_type="md", file_path="none", status=DocumentStatus.PROCESSING)
                db.add(document)
                await db.commit()
                await db.refresh(document)
                service = DocumentService(db)
                chunks = [ChunkData(content="same", chunk_index=0), ChunkData(content="next", chunk_index=1)]
                await service._save_chunks(document.id, chunks)
                await service._save_chunks(document.id, chunks)
                count = await db.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id))
                return count
        self.assertEqual(asyncio.run(scenario()), 2)

    def test_task_success_retry_failure_transitions_are_persisted(self):
        async def scenario():
            async with async_session() as db:
                service = TaskService(db)
                transition_key = f"phase3-transition-{uuid.uuid4()}"
                record, created = await service.create_or_get("document_processing", transition_key)
                self.assertTrue(created)
                same, created_again = await service.create_or_get("document_processing", transition_key)
                self.assertFalse(created_again)
                self.assertEqual(record.id, same.id)
                await service.mark_running(record, 1)
                await service.mark_retrying(record, RuntimeError("temporary"))
                self.assertEqual(record.status, TaskStatus.RETRYING)
                await service.mark_running(record, 2)
                await service.mark_succeeded(record, {"ok": True})
                self.assertEqual(record.status, TaskStatus.SUCCEEDED)

                failed, _ = await service.create_or_get("document_processing", f"phase3-failed-{uuid.uuid4()}")
                await service.mark_running(failed, 3)
                await service.mark_failed(failed, ValueError("permanent"))
                return failed.status, failed.attempt, failed.error_type
        status, attempt, error_type = asyncio.run(scenario())
        self.assertEqual(status, TaskStatus.FAILED)
        self.assertEqual(attempt, 3)
        self.assertEqual(error_type, "ValueError")

    def test_message_citations_and_agent_events_persist(self):
        async def scenario():
            async with async_session() as db:
                conversation = Conversation(title="events")
                db.add(conversation)
                await db.commit()
                await db.refresh(conversation)
                message = await persist_message_with_citations(db, conversation.id, "assistant", "answer", [{
                    "citation_number": 1, "document_title": "Doc", "document_id": "",
                    "section_title": "Section", "content_snippet": "evidence", "chunk_id": "",
                }])
                recorder = await AgentRunRecorder.create(db, "question", [])
                await recorder.event("plan", "completed", duration_ms=1.2, result_summary={"query_count": 1})
                await recorder.event("search", "completed", duration_ms=2.3, result_summary={"result_count": 2})
                await recorder.complete("answer")
                return str(message.id), recorder.run.status, recorder.sequence
        message_id, status, sequence = asyncio.run(scenario())
        self.assertTrue(message_id)
        self.assertEqual(status, "completed")
        self.assertEqual(sequence, 2)

    def test_upload_processing_retrieval_answer_and_citations_persist(self):
        celery_id = f"celery-{uuid.uuid4()}"
        marker = uuid.uuid4().hex
        query = f"What is stored? {marker}"
        answer = f"Durable evidence is stored [1]. {marker}"
        with patch("app.routers.documents.process_document_task.apply_async", return_value=SimpleNamespace(id=celery_id)):
            with TestClient(app) as client:
                uploaded = client.post(
                    "/api/documents/upload",
                    files={"file": ("integration.md", b"# Evidence\nInsightFlow stores durable evidence.", "text/markdown")},
                    headers={"Idempotency-Key": f"integration-{uuid.uuid4()}"},
                )
                self.assertEqual(uploaded.status_code, 200)
                document_id = uploaded.json()["id"]

        async def process_and_get_chunk():
            async with async_session() as db:
                with patch(
                    "app.services.embedding_service.EmbeddingService.embed_batch",
                    new=AsyncMock(return_value=[[0.1] * 384]),
                ):
                    await DocumentService(db).process_document(document_id)
                chunks = await DocumentService(db).get_chunks(document_id)
                return chunks[0]

        chunk = asyncio.run(process_and_get_chunk())
        result_item = {
            "chunk_id": str(chunk.id), "document_id": document_id,
            "document_title": "integration.md", "document_type": "md",
            "content": chunk.content, "chunk_index": chunk.chunk_index,
            "page_number": chunk.page_number, "section_title": chunk.section_title,
            "score": 1.0, "rrf_score": 0.03, "retrieval_channels": ["bm25", "vector"],
            "channel_ranks": {"bm25": 1, "vector": 1},
            "channel_scores": {"bm25": 1.0, "vector": 0.9}, "rrf_rank": 1, "fused_rank": 1,
        }
        diagnostics = {
            "explicit_document_scope": True, "selected_document_ids": [document_id],
            "missing_documents": [], "evidence_status": "sufficient", "evidence_message": None,
        }
        generated = {
            "answer": answer,
            "citations": [{
                "citation_number": 1, "document_title": "integration.md",
                "document_id": document_id, "section_title": chunk.section_title,
                "content_snippet": chunk.content, "chunk_id": str(chunk.id),
            }],
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16},
        }
        with (
            patch("app.services.embedding_service.EmbeddingService.embed_single", new=AsyncMock(return_value=[0.1] * 384)),
            patch("app.services.hybrid_service.HybridSearchService.search_with_diagnostics", new=AsyncMock(return_value=([result_item], diagnostics))),
            patch("app.services.rag_service.RAGService.generate", new=AsyncMock(return_value=generated)),
        ):
            with TestClient(app) as client:
                response = client.post("/api/chat", json={
                    "query": query, "document_ids": [document_id], "rerank": False,
                })
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["sources_used"], 1)
                self.assertEqual(payload["citations"][0]["chunk_id"], str(chunk.id))
                history = client.get(f"/api/chat/conversations/{payload['conversation_id']}")
                self.assertEqual(len(history.json()["messages"]), 2)

        async def persisted_counts():
            async with async_session() as db:
                retrievals = await db.scalar(select(func.count()).select_from(RetrievalRecord).where(RetrievalRecord.query == query))
                citations = await db.scalar(select(func.count()).select_from(Citation).where(Citation.chunk_id == chunk.id))
                messages = await db.scalar(select(func.count()).select_from(Message).where(Message.content == answer))
                return retrievals, citations, messages
        self.assertEqual(asyncio.run(persisted_counts()), (1, 1, 1))
        with TestClient(app) as client:
            client.delete(f"/api/documents/{document_id}")


if __name__ == "__main__":
    unittest.main()
