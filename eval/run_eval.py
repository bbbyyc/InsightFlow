from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import platform
import random
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent / "backend"
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(BACKEND_DIR))

from dataset import DatasetError, formal_readiness_errors, load_dataset, sha256_file
from metrics import (
    average_available,
    citation_accuracy,
    latency_summary,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)


MODES = ("bm25", "vector", "hybrid")
REPORT_SCHEMA_VERSION = "2.0"
JUDGE_PROMPT_TEMPLATE = """You are evaluating whether an answer is faithful to supplied source excerpts.

Question: {question}
Answer: {answer}
Source excerpts:
{sources}

Score faithfulness from 1 to 5. Consider only whether claims are supported by the excerpts; do not use outside knowledge.
Return JSON only: {{"faithfulness": 1, "unsupported_claims": ["..."], "reason": "..."}}"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def environment_metadata(args, dataset: dict, dataset_path: Path) -> dict:
    try:
        from app.config import settings
        embedding_model = settings.embedding_model
        llm_model = settings.deepseek_model
    except Exception:
        embedding_model = os.getenv("EMBEDDING_MODEL", "unavailable")
        llm_model = os.getenv("DEEPSEEK_MODEL", "unavailable")
    return {
        "run_started_at": utc_now(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "dataset_path": str(dataset_path),
        "dataset_sha256": sha256_file(dataset_path),
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "dataset_purpose": dataset["dataset_purpose"],
        "corpus_manifest": dataset.get("corpus_manifest"),
        "corpus_id": None,
        "corpus_version": None,
        "corpus_manifest_sha256": None,
        "embedding_model": embedding_model,
        "llm_model": llm_model,
        "top_k": args.top_k,
        "candidate_multiplier": args.candidate_multiplier,
        "rrf_k": 60,
        "random_seed": args.seed,
        "generation_enabled": args.include_generation,
        "llm_judge_enabled": args.llm_judge,
        "judge_temperature": 0,
        "modes": list(MODES),
    }


def metric_values(case: dict, chunks: list[dict], top_k: int) -> dict:
    chunk_ids = [str(chunk["chunk_id"]) for chunk in chunks]
    document_ids = [str(chunk.get("document_id", "")) for chunk in chunks]
    relevant_chunks = set(case["relevant_chunk_ids"])
    relevant_documents = set(case["relevant_document_ids"])
    return {
        "recall_at_5": recall_at_k(chunk_ids, relevant_chunks, min(5, top_k)),
        "mrr": reciprocal_rank(chunk_ids, relevant_chunks),
        "ndcg_at_5": ndcg_at_k(chunk_ids, relevant_chunks, min(5, top_k)),
        "document_recall_at_5": recall_at_k(document_ids, relevant_documents, min(5, top_k)),
    }


def normalize_usage(generation_result: dict | None) -> dict:
    if not generation_result:
        return {"status": "unavailable", "prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    usage = generation_result.get("token_usage") or {}
    values = [usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens")]
    if any(value is None for value in values) or not any(values):
        return {"status": "unavailable", "prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    return {
        "status": "reported_by_api",
        "prompt_tokens": int(values[0]),
        "completion_tokens": int(values[1]),
        "total_tokens": int(values[2]),
    }


async def judge_faithfulness(case: dict, answer: str, chunks: list[dict], args) -> dict:
    from app.config import settings
    import httpx

    sources = "\n\n".join(f"[{index}] {chunk['content']}" for index, chunk in enumerate(chunks, 1))
    prompt = JUDGE_PROMPT_TEMPLATE.format(question=case["query"], answer=answer, sources=sources)
    requested_at = utc_now()
    raw_output = None
    parsed = None
    error = None
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{settings.deepseek_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"},
                json={
                    "model": settings.deepseek_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 600,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            payload = response.json()
            raw_output = payload["choices"][0]["message"]["content"]
            parsed = json.loads(raw_output)
            usage = payload.get("usage")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        usage = None
    return {
        "status": "available" if parsed is not None else "failed",
        "model": settings.deepseek_model,
        "prompt": prompt,
        "temperature": 0,
        "requested_at": requested_at,
        "completed_at": utc_now(),
        "raw_output": raw_output,
        "parsed_result": parsed,
        "api_usage": usage,
        "error": error,
    }


async def real_retrieve(mode: str, case: dict, args, services: dict) -> list[dict]:
    document_ids = case.get("document_filter_ids") or args.corpus_document_ids or None
    top_k = args.top_k
    candidate_k = top_k * args.candidate_multiplier
    if mode == "bm25":
        results = await services["bm25"].search(case["query"], top_k=top_k, document_ids=document_ids)
        return [vars(result) for result in results]

    query_embedding = await services["embedding"].embed_single(case["query"])
    if mode == "vector":
        return await services["retrieval"].vector_search(query_embedding, top_k=top_k, document_ids=document_ids)
    return await services["hybrid"].search(
        query=case["query"],
        query_embedding=query_embedding,
        vector_top_k=candidate_k,
        bm25_top_k=candidate_k,
        final_top_k=top_k,
        document_ids=document_ids,
    )


def fixture_retrieve(mode: str, case: dict, args) -> list[dict]:
    rankings = case["fixture_rankings"][mode][:args.top_k]
    documents = case["fixture_documents"]
    return [
        {
            "chunk_id": chunk_id,
            "document_id": documents[chunk_id],
            "document_title": documents[chunk_id],
            "section_title": "synthetic",
            "content": f"Synthetic content for {chunk_id}",
            "score": 1.0 / rank,
        }
        for rank, chunk_id in enumerate(rankings, 1)
    ]


async def evaluate_case_mode(case: dict, mode: str, args, services=None) -> dict:
    started = time.perf_counter()
    error = None
    chunks = []
    generation = None
    generation_latency_ms = None
    judge = None
    try:
        if args.fixture:
            chunks = fixture_retrieve(mode, case, args)
        else:
            chunks = await real_retrieve(mode, case, args, services)
        retrieval_latency_ms = (time.perf_counter() - started) * 1000

        if args.include_generation:
            generation_started = time.perf_counter()
            generation = await services["rag"].generate(case["query"], chunks)
            generation_latency_ms = (time.perf_counter() - generation_started) * 1000
            if args.llm_judge:
                judge = await judge_faithfulness(case, generation["answer"], chunks, args)
    except Exception as exc:
        retrieval_latency_ms = (time.perf_counter() - started) * 1000
        error = {"category": "runtime_error", "type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}

    answer = generation.get("answer", "") if generation else ""
    context_ids = [str(chunk["chunk_id"]) for chunk in chunks]
    metrics = metric_values(case, chunks, args.top_k) if not error else {
        "recall_at_5": None, "mrr": None, "ndcg_at_5": None, "document_recall_at_5": None,
    }
    metrics["citation_accuracy"] = (
        citation_accuracy(answer, context_ids, set(case["relevant_chunk_ids"]))
        if generation else None
    )
    metrics["answer_faithfulness"] = (
        (judge or {}).get("parsed_result", {}).get("faithfulness")
        if (judge or {}).get("parsed_result") else None
    )
    return {
        "case_id": case["id"],
        "mode": mode,
        "query": case["query"],
        "question_type": case["question_type"],
        "document_filter_ids": case.get("document_filter_ids", []),
        "gold": {
            "relevant_document_ids": case["relevant_document_ids"],
            "relevant_chunk_ids": case["relevant_chunk_ids"],
            "label_review_status": case["label_review_status"],
            "data_source": case["data_source"],
        },
        "retrieved": [
            {
                "rank": rank,
                "chunk_id": str(chunk["chunk_id"]),
                "document_id": str(chunk.get("document_id", "")),
                "section_title": chunk.get("section_title"),
                "score": chunk.get("score"),
                "retrieval_channels": chunk.get("retrieval_channels", [mode]),
                "channel_ranks": chunk.get("channel_ranks", {mode: rank}),
            }
            for rank, chunk in enumerate(chunks, 1)
        ],
        "metrics": metrics,
        "retrieval_latency_ms": retrieval_latency_ms,
        "generation_latency_ms": generation_latency_ms,
        "total_latency_ms": (time.perf_counter() - started) * 1000,
        "answer": answer if generation else None,
        "citations": generation.get("citations") if generation else None,
        "token_usage": normalize_usage(generation),
        "faithfulness_judge": judge,
        "error": error,
    }


async def create_services():
    from app.database import async_session
    from app.services.embedding_service import EmbeddingService
    from app.services.retrieval_service import RetrievalService
    from app.services.bm25_service import BM25Service
    from app.services.hybrid_service import HybridSearchService
    from app.services.rag_service import RAGService

    session_context = async_session()
    db = await session_context.__aenter__()
    retrieval = RetrievalService(db)
    bm25 = BM25Service(db)
    return session_context, {
        "db": db,
        "retrieval": retrieval,
        "bm25": bm25,
        "hybrid": HybridSearchService(retrieval, bm25),
        "embedding": EmbeddingService(),
        "rag": RAGService(),
    }


def load_approved_corpus(dataset_path: Path, dataset: dict) -> tuple[dict, Path]:
    reference = dataset.get("corpus_manifest")
    if not reference:
        raise DatasetError("formal dataset must reference corpus_manifest")
    path = (dataset_path.parent / reference).resolve()
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("status") != "approved":
        raise DatasetError(f"corpus manifest status must be approved, got {manifest.get('status')}")
    if not manifest.get("corpus_id") or not manifest.get("corpus_version"):
        raise DatasetError("corpus manifest must include corpus_id and corpus_version")
    if not manifest.get("documents"):
        raise DatasetError("approved corpus manifest must include documents")
    for document in manifest["documents"]:
        if not document.get("document_id") or not document.get("sha256"):
            raise DatasetError("every corpus document must include document_id and sha256")
    return manifest, path


async def verify_database_corpus(manifest: dict, services: dict) -> None:
    from sqlalchemy import text

    expected = {str(document["document_id"]): document for document in manifest["documents"]}
    result = await services["db"].execute(
        text("SELECT id::text, title FROM documents WHERE id::text = ANY(:ids)"),
        {"ids": list(expected)},
    )
    actual = {row[0]: row[1] for row in result.fetchall()}
    missing = sorted(set(expected) - set(actual))
    if missing:
        raise DatasetError(f"database is missing frozen corpus documents: {missing}")
    for document_id, document in expected.items():
        if document.get("title") and actual[document_id] != document["title"]:
            raise DatasetError(f"document title mismatch for {document_id}")


def summarize(rows: list[dict], metadata: dict, status: str) -> dict:
    by_mode = {}
    for mode in MODES:
        mode_rows = [row for row in rows if row["mode"] == mode]
        by_mode[mode] = {
            "cases": len(mode_rows),
            "successful_cases": sum(row["error"] is None for row in mode_rows),
            "recall_at_5": average_available(row["metrics"]["recall_at_5"] for row in mode_rows),
            "mrr": average_available(row["metrics"]["mrr"] for row in mode_rows),
            "ndcg_at_5": average_available(row["metrics"]["ndcg_at_5"] for row in mode_rows),
            "citation_accuracy": average_available(row["metrics"]["citation_accuracy"] for row in mode_rows),
            "answer_faithfulness": average_available(row["metrics"]["answer_faithfulness"] for row in mode_rows),
            "retrieval_latency": latency_summary([row["retrieval_latency_ms"] for row in mode_rows if row["error"] is None]),
            "generation_latency": latency_summary([
                row["generation_latency_ms"] for row in mode_rows
                if row["error"] is None and row["generation_latency_ms"] is not None
            ]),
            "total_latency": latency_summary([row["total_latency_ms"] for row in mode_rows if row["error"] is None]),
            "average_total_tokens": average_available(row["token_usage"]["total_tokens"] for row in mode_rows),
            "token_usage_status": "available" if any(row["token_usage"]["status"] == "reported_by_api" for row in mode_rows) else "unavailable",
        }
    return {"report_schema_version": REPORT_SCHEMA_VERSION, "status": status, "metadata": metadata, "metrics_by_mode": by_mode}


def write_comparison_csv(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "mode", "cases", "successful_cases", "recall_at_5", "mrr", "ndcg_at_5",
            "citation_accuracy", "answer_faithfulness", "average_latency_ms", "p50_latency_ms",
            "p95_latency_ms", "average_total_tokens", "token_usage_status",
        ])
        writer.writeheader()
        for mode in MODES:
            values = summary["metrics_by_mode"].get(mode, {})
            latency = values.get("retrieval_latency", {})
            writer.writerow({
                "mode": mode,
                "cases": values.get("cases", 0),
                "successful_cases": values.get("successful_cases", 0),
                "recall_at_5": values.get("recall_at_5"),
                "mrr": values.get("mrr"),
                "ndcg_at_5": values.get("ndcg_at_5"),
                "citation_accuracy": values.get("citation_accuracy"),
                "answer_faithfulness": values.get("answer_faithfulness"),
                "average_latency_ms": latency.get("average_ms"),
                "p50_latency_ms": latency.get("p50_ms"),
                "p95_latency_ms": latency.get("p95_ms"),
                "average_total_tokens": values.get("average_total_tokens"),
                "token_usage_status": values.get("token_usage_status", "unavailable"),
            })


def write_markdown(path: Path, summary: dict, failures: list[dict]) -> None:
    metadata = summary["metadata"]
    purpose = metadata["dataset_purpose"]
    warning = (
        "> **合成 Fixture，仅验证公式和报告管线；以下数字不是 InsightFlow 项目效果。**"
        if purpose == "synthetic_formula_validation"
        else "> 本报告来自人工审核 Gold Label。" if summary["status"] == "completed" else "> **评测被阻止，未生成项目效果数字。**"
    )
    lines = [
        "# 检索消融评测报告", "", warning, "",
        f"- 状态：`{summary['status']}`",
        f"- 数据集：`{metadata['dataset_id']}@{metadata['dataset_version']}`",
        f"- 数据 SHA-256：`{metadata['dataset_sha256']}`",
        f"- Top-K：`{metadata['top_k']}`",
        f"- 随机种子：`{metadata['random_seed']}`",
        f"- Embedding：`{metadata['embedding_model']}`",
        f"- LLM：`{metadata['llm_model']}`",
        "", "## 三方案同条件结果", "",
        "| 方案 | Recall@5 | MRR | nDCG@5 | 引用准确性 | 忠实度 | 平均延迟(ms) | P50 | P95 | 平均 Token |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in MODES:
        value = summary["metrics_by_mode"].get(mode, {})
        latency = value.get("retrieval_latency", {})
        display = lambda item: "不可用" if item is None else f"{item:.4f}" if isinstance(item, float) else str(item)
        lines.append("| " + " | ".join([
            mode, display(value.get("recall_at_5")), display(value.get("mrr")), display(value.get("ndcg_at_5")),
            display(value.get("citation_accuracy")), display(value.get("answer_faithfulness")),
            display(latency.get("average_ms")), display(latency.get("p50_ms")), display(latency.get("p95_ms")),
            display(value.get("average_total_tokens")),
        ]) + " |")
    lines.extend(["", "## 失败与错误分类", ""])
    if failures:
        for failure in failures:
            lines.append(f"- `{failure.get('case_id', 'run')}` / `{failure.get('mode', 'all')}`：{failure['category']} — {failure['message']}")
    else:
        lines.append("无。")
    lines.extend([
        "", "## 指标口径", "",
        "- Recall@5：前 5 个结果中的唯一相关 Chunk 数 / Gold 相关 Chunk 总数。",
        "- MRR：首个相关 Chunk 排名的倒数；无命中为 0。",
        "- nDCG@5：二元相关性的 DCG@5 / 理想 DCG@5。",
        "- 引用准确性：答案中引用位置映射到 Gold 相关 Chunk 的数量 / 全部引用编号数量。没有生成答案、引用或 Gold 时不可用。",
        "- 回答忠实度：仅在显式启用 LLM Judge 时可用；Judge 原始 prompt、模型、温度、时间、原始输出和解析结果保存在逐题 JSONL。",
        "- 延迟：同一进程、同一数据与 Top-K 下的实测检索 wall-clock；汇总平均值及线性插值 P50/P95。",
        "- `summary.json` 另分别记录检索、生成和端到端总延迟的 count、平均、P50、P95；未启用生成时生成延迟为空。Markdown/CSV 主表展示检索延迟，保证三种检索方案可比。",
        "- Token：仅读取生成 API 返回的 usage；缺失或为零时标记不可用，不做估算。",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_artifacts(output_dir: Path, rows: list[dict], summary: dict, failures: list[dict]) -> None:
    write_jsonl(output_dir / "cases.jsonl", rows)
    json_dump(output_dir / "summary.json", summary)
    write_comparison_csv(output_dir / "comparison.csv", summary)
    write_markdown(output_dir / "report.md", summary, failures)
    json_dump(output_dir / "failures.json", {"count": len(failures), "failures": failures})


async def execute(args) -> int:
    dataset_path = Path(args.dataset).resolve()
    output_dir = Path(args.output_dir).resolve()
    try:
        dataset = load_dataset(dataset_path)
    except (OSError, json.JSONDecodeError, DatasetError) as exc:
        print(f"Dataset validation failed: {exc}", file=sys.stderr)
        return 2

    metadata = environment_metadata(args, dataset, dataset_path)
    readiness = formal_readiness_errors(dataset)
    if args.fixture:
        if dataset["dataset_purpose"] != "synthetic_formula_validation":
            print("--fixture only accepts dataset_purpose=synthetic_formula_validation", file=sys.stderr)
            return 2
    elif readiness:
        failures = [{"case_id": "run", "mode": "all", "category": "unreviewed_gold_labels", "message": message} for message in readiness]
        summary = summarize([], metadata, "blocked_unreviewed_labels")
        write_artifacts(output_dir, [], summary, failures)
        print("Formal evaluation refused: dataset is not human-approved. See failures.json.", file=sys.stderr)
        return 3

    corpus_manifest = None
    if not args.fixture:
        try:
            corpus_manifest, corpus_path = load_approved_corpus(dataset_path, dataset)
        except (OSError, json.JSONDecodeError, DatasetError) as exc:
            failures = [{"case_id": "run", "mode": "all", "category": "corpus_not_approved", "message": str(exc)}]
            summary = summarize([], metadata, "blocked_corpus_manifest")
            write_artifacts(output_dir, [], summary, failures)
            print(f"Formal evaluation refused: {exc}", file=sys.stderr)
            return 3
        args.corpus_document_ids = [str(document["document_id"]) for document in corpus_manifest["documents"]]
        metadata.update({
            "corpus_id": corpus_manifest["corpus_id"],
            "corpus_version": corpus_manifest["corpus_version"],
            "corpus_manifest_sha256": sha256_file(corpus_path),
        })
    else:
        args.corpus_document_ids = []

    random.seed(args.seed)
    services = None
    session_context = None
    if not args.fixture:
        try:
            session_context, services = await create_services()
            await verify_database_corpus(corpus_manifest, services)
        except Exception as exc:
            failures = [{"case_id": "run", "mode": "all", "category": "environment_unavailable", "message": f"{type(exc).__name__}: {exc}"}]
            summary = summarize([], metadata, "blocked_environment")
            write_artifacts(output_dir, [], summary, failures)
            print(f"Evaluation environment unavailable: {exc}", file=sys.stderr)
            return 4

    rows = []
    try:
        for case in dataset["cases"]:
            for mode in MODES:
                rows.append(await evaluate_case_mode(case, mode, args, services))
    finally:
        if session_context is not None:
            await session_context.__aexit__(None, None, None)

    failures = []
    for row in rows:
        if row["error"]:
            failures.append({"case_id": row["case_id"], "mode": row["mode"], **row["error"]})
        elif row["metrics"]["recall_at_5"] == 0:
            failures.append({"case_id": row["case_id"], "mode": row["mode"], "category": "no_relevant_chunk_in_top5", "message": "No Gold relevant chunk was retrieved in the first five results."})
        if (row.get("faithfulness_judge") or {}).get("status") == "failed":
            failures.append({"case_id": row["case_id"], "mode": row["mode"], "category": "judge_failure", "message": row["faithfulness_judge"]["error"]})

    status = "synthetic_fixture_completed" if args.fixture else "completed"
    summary = summarize(rows, metadata, status)
    write_artifacts(output_dir, rows, summary, failures)
    print(f"Wrote evaluation artifacts to {output_dir}")
    return 0 if not any(row["error"] for row in rows) else 5


def parse_args():
    parser = argparse.ArgumentParser(description="InsightFlow reproducible three-way retrieval ablation")
    parser.add_argument("--dataset", default=str(EVAL_DIR / "datasets" / "candidate_project_v1.json"))
    parser.add_argument("--output-dir", default=str(EVAL_DIR / "reports" / "latest"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-multiplier", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--fixture", action="store_true", help="Run synthetic formula/report validation; never a project-quality evaluation")
    parser.add_argument("--include-generation", action="store_true", help="Call the configured answer model and read real API usage")
    parser.add_argument("--llm-judge", action="store_true", help="Record a faithfulness judge call; requires --include-generation")
    args = parser.parse_args()
    if args.top_k <= 0 or args.candidate_multiplier <= 0:
        parser.error("--top-k and --candidate-multiplier must be positive")
    if args.llm_judge and not args.include_generation:
        parser.error("--llm-judge requires --include-generation")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(execute(parse_args())))
