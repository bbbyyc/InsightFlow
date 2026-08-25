"""Real-stack API acceptance. It never substitutes or mocks model, queue, or database calls."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


def wait_task(client: httpx.Client, api: str, task_id: str, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"{api}/api/tasks/{task_id}").json()
        print(f"task {task_id}: {task['status']} {task['progress']}% attempt={task['attempt']}")
        if task["status"] in {"succeeded", "failed", "cancelled"}:
            return task
        time.sleep(2)
    raise TimeoutError(f"task {task_id} did not finish in {timeout}s")


def upload(client: httpx.Client, api: str, path: Path) -> dict:
    with path.open("rb") as handle:
        response = client.post(
            f"{api}/api/documents/upload",
            files={"file": (path.name, handle, "text/markdown")},
            headers={"Idempotency-Key": f"phase5:{path.name}:{path.stat().st_mtime_ns}"},
        )
    response.raise_for_status()
    return response.json()


def read_sse(response: httpx.Response) -> tuple[list[str], dict | None]:
    events, terminal = [], None
    event_name = None
    for line in response.iter_lines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            payload = json.loads(line.split(":", 1)[1].strip())
            name = event_name or payload.get("event", "message")
            events.append(name)
            if name in {"complete", "failed"}:
                terminal = payload
    return events, terminal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--demo-dir", type=Path, default=Path("/demo"))
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    with httpx.Client(timeout=180) as client:
        ready = client.get(f"{args.api}/api/ready")
        ready.raise_for_status()
        print(json.dumps(ready.json(), ensure_ascii=False))

        uploaded = [upload(client, args.api, path) for path in sorted(args.demo_dir.glob("*.md"))]
        if len(uploaded) < 2:
            raise RuntimeError("at least two demo Markdown documents are required")
        for item in uploaded:
            task = wait_task(client, args.api, item["task"]["id"], args.timeout)
            if task["status"] != "succeeded":
                raise RuntimeError(f"document processing failed: {task}")

        document_ids = [item["id"] for item in uploaded]
        conversation = client.post(
            f"{args.api}/api/chat/conversations",
            json={"title": "Phase 5 E2E", "document_ids": document_ids},
        )
        conversation.raise_for_status()
        conversation_id = conversation.json()["id"]

        for mode in ("bm25", "vector", "hybrid"):
            response = client.post(
                f"{args.api}/api/search",
                json={"query": "InsightFlow 如何处理文档并返回引用？", "mode": mode, "top_k": 5, "document_ids": document_ids},
            )
            response.raise_for_status()
            results = response.json()["results"]
            if not results:
                raise RuntimeError(f"{mode} returned no real results")
            print(f"{mode}: {len(results)} results")

        with client.stream(
            "POST", f"{args.api}/api/agent/stream",
            json={"query": "文档处理链路和可点击引用是如何实现的？", "document_ids": document_ids, "conversation_id": conversation_id, "max_iterations": 2},
        ) as response:
            response.raise_for_status()
            events, terminal = read_sse(response)
        required = {"accepted", "plan", "search", "generate", "complete"}
        if not required.issubset(events) or not terminal or terminal.get("event") != "complete":
            raise RuntimeError(f"SSE did not complete: events={events}, terminal={terminal}")
        result = terminal["result"]
        if not result.get("citations"):
            raise RuntimeError("completed answer did not contain real citations")

        restored = client.get(f"{args.api}/api/chat/conversations/{conversation_id}")
        restored.raise_for_status()
        if len(restored.json().get("messages", [])) < 2:
            raise RuntimeError("conversation was not persisted")
        print(json.dumps({"conversation_id": conversation_id, "events": events, "citations": len(result["citations"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
