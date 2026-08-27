"""Upload demo documents through the public API and wait for real worker completion."""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import time
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    uploaded = []
    with httpx.Client(timeout=60) as client:
        for path in args.paths:
            if not path.is_file():
                raise SystemExit(f"Demo document not found: {path}")
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            idempotency_source = f"{path.resolve()}:{path.stat().st_mtime_ns}"
            idempotency_key = "demo:" + hashlib.sha256(idempotency_source.encode("utf-8")).hexdigest()
            with path.open("rb") as handle:
                response = client.post(
                    f"{args.api}/api/documents/upload",
                    files={"file": (path.name, handle, mime)},
                    headers={"Idempotency-Key": idempotency_key},
                )
            response.raise_for_status()
            payload = response.json()
            uploaded.append((payload["id"], payload["task"]["id"]))
            print(json.dumps(payload, ensure_ascii=False))

        deadline = time.monotonic() + args.timeout
        pending = dict(uploaded)
        while pending and time.monotonic() < deadline:
            for document_id, task_id in list(pending.items()):
                task = client.get(f"{args.api}/api/tasks/{task_id}").json()
                print(f"{document_id}: {task['status']} {task['progress']}%")
                if task["status"] == "succeeded":
                    pending.pop(document_id)
                elif task["status"] in {"failed", "cancelled"}:
                    raise SystemExit(f"Demo import failed: {task.get('error_type')}: {task.get('error_message')}")
            if pending:
                time.sleep(2)
    if pending:
        raise SystemExit(f"Timed out waiting for tasks: {pending}")
    print("Demo import completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
