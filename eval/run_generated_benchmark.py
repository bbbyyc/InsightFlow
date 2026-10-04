from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


SEED = 20260828
PREFIX = "IFBENCH-20260828"
MODES = ("bm25", "vector", "hybrid")

SPECS = [
    ("苍穹", "QK-731", "林岚", "上海", "识别供应链发票中的重复付款", "12分钟", "票据风控"),
    ("灯塔", "LH-284", "周衡", "北京", "预测冷链运输中的温度异常", "18分钟", "冷链监控"),
    ("星轨", "OR-619", "顾言", "深圳", "发现卫星遥测数据中的姿态漂移", "9分钟", "航天遥测"),
    ("青禾", "GH-452", "沈秋", "杭州", "评估农田灌溉计划的节水潜力", "25分钟", "智慧农业"),
    ("砺石", "LS-903", "陈砚", "成都", "检测矿山设备的轴承早期故障", "14分钟", "工业运维"),
    ("潮汐", "TD-176", "宋澜", "厦门", "预测港口潮位对靠泊窗口的影响", "21分钟", "港航调度"),
    ("霁云", "JC-548", "许晴", "南京", "识别云资源账单中的闲置实例", "16分钟", "云成本治理"),
    ("玄鸟", "XN-327", "陆遥", "西安", "分析无人机巡检图像中的绝缘子裂纹", "11分钟", "电网巡检"),
    ("白泽", "BZ-865", "韩川", "武汉", "归并医疗术语中的同义疾病编码", "19分钟", "医疗编码"),
    ("扶摇", "FY-294", "赵翼", "重庆", "估计山地风电机组的短期功率", "13分钟", "新能源预测"),
    ("赤霄", "CX-710", "唐锋", "天津", "定位仓储机器人路径拥堵的根因", "17分钟", "仓储调度"),
    ("静水", "JS-438", "叶澄", "苏州", "筛查客服录音中的合规风险表述", "22分钟", "客服质检"),
    ("衡山", "HS-592", "魏岳", "长沙", "校验桥梁传感器的结构应力异常", "15分钟", "桥梁监测"),
    ("墨子", "MZ-146", "秦墨", "合肥", "优化芯片版图中的布线拥塞", "8分钟", "芯片设计"),
    ("鲲鹏", "KP-837", "袁航", "青岛", "预测集装箱在转运节点的滞留时间", "24分钟", "物流预测"),
    ("云雀", "YL-365", "夏音", "广州", "识别直播音频中的版权音乐片段", "10分钟", "内容版权"),
    ("天工", "TG-921", "鲁班", "无锡", "生成数控加工参数的节拍优化建议", "20分钟", "智能制造"),
    ("归藏", "GZ-483", "易安", "济南", "发现保险理赔材料中的证据矛盾", "27分钟", "保险审核"),
    ("望舒", "WS-258", "苏月", "昆明", "预测城市路灯的分区能耗峰值", "23分钟", "城市能源"),
    ("海若", "HR-674", "江宁", "宁波", "识别海洋浮标数据中的传感器漂移", "26分钟", "海洋观测"),
]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def latency(values: list[float]) -> dict:
    return {
        "count": len(values),
        "average_ms": statistics.fmean(values),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "max_ms": max(values),
    }


def reciprocal_rank(ids: list[str], gold: set[str]) -> float:
    for rank, item in enumerate(ids, 1):
        if item in gold:
            return 1.0 / rank
    return 0.0


def ndcg(ids: list[str], gold: set[str]) -> float:
    dcg = sum(1.0 / math.log2(rank + 1) for rank, item in enumerate(ids, 1) if item in gold)
    ideal_hits = min(len(gold), len(ids))
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def document_text(spec: tuple[str, ...], index: int) -> str:
    name, code, owner, region, purpose, recovery, domain = spec
    distractors = ", ".join(item[6] for item in SPECS if item != spec)[:180]
    return (
        f"# {PREFIX} {name}系统\n\n"
        f"## 身份信息\n{name}系统的内部编号为 {code}，负责人是{owner}，主部署区域为{region}。\n\n"
        f"## 核心任务\n该系统用于{purpose}，所属方向是{domain}。"
        f"发生故障时，服务恢复目标为{recovery}。\n\n"
        f"## 边界说明\n编号 {code} 仅对应{name}系统，不对应其他项目。"
        f"相关但不同的业务方向包括：{distractors}。基准序号为 {index:02d}。\n"
    )


def queries(spec: tuple[str, ...]) -> list[tuple[str, str, str]]:
    name, code, owner, region, purpose, recovery, domain = spec
    return [
        (f"内部编号 {code} 对应哪个系统？", "exact_code", code),
        (f"谁负责{name}系统？", "entity_fact", owner),
        (f"哪个系统部署在{region}并用于{purpose}？", "compound_fact", purpose),
        (f"如果要{purpose}，应该使用什么系统？", "semantic_purpose", purpose),
        (f"{domain}方向的{name}系统服务恢复目标是多少？", "recovery_fact", recovery),
    ]


def wait_document(client: httpx.Client, api: str, document_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"{api}/api/documents/{document_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] == "completed":
            return payload
        if payload["status"] == "failed":
            raise RuntimeError(f"document {document_id} failed: {payload.get('error_message')}")
        time.sleep(0.5)
    raise TimeoutError(f"document {document_id} did not complete in {timeout}s")


def search(client: httpx.Client, api: str, query: str, mode: str, document_ids: list[str], top_k: int) -> tuple[dict, float]:
    started = time.perf_counter()
    response = client.post(
        f"{api}/api/search",
        json={"query": query, "mode": mode, "top_k": top_k, "document_ids": document_ids},
    )
    elapsed = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return response.json(), elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic generated-corpus retrieval benchmark")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="eval/reports/generated_benchmark_20260828")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--concurrent-requests", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    output = Path(args.output)
    corpus_dir = output / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()
    run_key = f"{PREFIX}:{time.time_ns()}"

    with httpx.Client(timeout=args.timeout) as client:
        health = client.get(f"{args.api}/api/ready")
        health.raise_for_status()
        existing = client.get(f"{args.api}/api/documents").json()
        for document in existing:
            if str(document.get("title", "")).startswith(PREFIX):
                client.delete(f"{args.api}/api/documents/{document['id']}").raise_for_status()

        documents = []
        ingestion_started = time.perf_counter()
        for index, spec in enumerate(SPECS, 1):
            filename = f"{PREFIX}-{index:02d}-{spec[0]}.md"
            content = document_text(spec, index).encode("utf-8")
            source_path = corpus_dir / filename
            source_path.write_bytes(content)
            response = client.post(
                f"{args.api}/api/documents/upload",
                files={"file": (filename, content, "text/markdown")},
                headers={"Idempotency-Key": f"{run_key}:{index:02d}"},
            )
            response.raise_for_status()
            documents.append({"index": index, "spec": spec, "id": response.json()["id"], "filename": filename, "bytes": len(content)})

        processing_times = []
        total_chunks = 0
        for document in documents:
            item_started = time.perf_counter()
            payload = wait_document(client, args.api, document["id"], args.timeout)
            processing_times.append((time.perf_counter() - item_started) * 1000)
            document["chunks"] = payload["chunks"]
            document["chunk_ids"] = [chunk["id"] for chunk in payload["chunks"]]
            document["chunk_count"] = payload["chunk_count"]
            total_chunks += payload["chunk_count"]
        ingestion_total_ms = (time.perf_counter() - ingestion_started) * 1000
        document_ids = [document["id"] for document in documents]

        duplicate = documents[0]
        duplicate_content = (corpus_dir / duplicate["filename"]).read_bytes()
        duplicate_response = client.post(
            f"{args.api}/api/documents/upload",
            files={"file": (duplicate["filename"], duplicate_content, "text/markdown")},
            headers={"Idempotency-Key": f"{run_key}:01"},
        )
        duplicate_response.raise_for_status()
        duplicate_payload = duplicate_response.json()
        duplicate_detail = client.get(f"{args.api}/api/documents/{duplicate['id']}").json()

        cases = []
        for document in documents:
            for number, (query, query_type, answer_key) in enumerate(queries(document["spec"]), 1):
                gold_chunks = [chunk["id"] for chunk in document["chunks"] if answer_key in chunk["content"]]
                if not gold_chunks:
                    raise RuntimeError(f"No generated Gold chunk contains {answer_key!r}")
                cases.append({
                    "id": f"q{document['index']:02d}-{number}", "query": query,
                    "query_type": query_type, "gold_document_id": document["id"], "gold_chunk_ids": gold_chunks,
                })

        for mode in MODES:
            for case in cases[:3]:
                search(client, args.api, case["query"], mode, document_ids, args.top_k)

        rows = []
        for mode in MODES:
            for case in cases:
                payload, elapsed = search(client, args.api, case["query"], mode, document_ids, args.top_k)
                ids = [item["chunk_id"] for item in payload["results"]]
                doc_ids = [item["document_id"] for item in payload["results"]]
                gold = set(case["gold_chunk_ids"])
                hits = len(set(ids[:5]) & gold)
                rows.append({
                    **case, "mode": mode, "retrieved_chunk_ids": ids, "retrieved_document_ids": doc_ids,
                    "recall_at_5": hits / len(gold),
                    "document_recall_at_5": 1.0 if case["gold_document_id"] in doc_ids[:5] else 0.0,
                    "mrr": reciprocal_rank(ids, gold),
                    "ndcg_at_5": ndcg(ids[:5], gold), "latency_ms": elapsed,
                })

        metrics = {}
        for mode in MODES:
            selected = [row for row in rows if row["mode"] == mode]
            metrics[mode] = {
                "cases": len(selected),
                "recall_at_5": statistics.fmean(row["recall_at_5"] for row in selected),
                "document_recall_at_5": statistics.fmean(row["document_recall_at_5"] for row in selected),
                "mrr": statistics.fmean(row["mrr"] for row in selected),
                "ndcg_at_5": statistics.fmean(row["ndcg_at_5"] for row in selected),
                "latency": latency([row["latency_ms"] for row in selected]),
            }

        concurrent_cases = [cases[index % len(cases)] for index in range(args.concurrent_requests)]
        concurrent_started = time.perf_counter()
        concurrent_latencies = []
        failures = []

        def one_request(case: dict) -> float:
            with httpx.Client(timeout=args.timeout) as thread_client:
                _, elapsed = search(thread_client, args.api, case["query"], "hybrid", document_ids, args.top_k)
                return elapsed

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [executor.submit(one_request, case) for case in concurrent_cases]
            for future in concurrent.futures.as_completed(futures):
                try:
                    concurrent_latencies.append(future.result())
                except Exception as exc:
                    failures.append(f"{type(exc).__name__}: {exc}")
        concurrent_elapsed = time.perf_counter() - concurrent_started

    report = {
        "status": "completed",
        "benchmark_kind": "deterministic_generated_corpus",
        "warning": "Programmatically generated benchmark; not a human-labeled production dataset.",
        "seed": SEED,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "api": args.api,
        "corpus": {
            "documents": len(documents), "chunks": total_chunks, "queries": len(cases),
            "bytes": sum(document["bytes"] for document in documents),
            "sha256": hashlib.sha256(b"".join((corpus_dir / document["filename"]).read_bytes() for document in documents)).hexdigest(),
        },
        "ingestion": {
            "total_ms": ingestion_total_ms,
            "documents_per_second": len(documents) / (ingestion_total_ms / 1000),
            "chunks_per_second": total_chunks / (ingestion_total_ms / 1000),
            "poll_completion_observation": latency(processing_times),
        },
        "idempotency": {
            "same_document_id": duplicate_payload["id"] == duplicate["id"],
            "created_on_duplicate": duplicate_payload.get("created"),
            "chunk_count_before": duplicate["chunk_count"],
            "chunk_count_after": duplicate_detail["chunk_count"],
        },
        "metrics_by_mode": metrics,
        "concurrency": {
            "workers": args.concurrency, "requests": args.concurrent_requests,
            "successes": len(concurrent_latencies), "failures": len(failures),
            "success_rate": len(concurrent_latencies) / args.concurrent_requests,
            "wall_seconds": concurrent_elapsed,
            "throughput_rps": len(concurrent_latencies) / concurrent_elapsed,
            "latency": latency(concurrent_latencies) if concurrent_latencies else None,
            "failure_samples": failures[:10],
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "cases.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
