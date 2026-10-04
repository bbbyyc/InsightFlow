"""Read-only, real-corpus retrieval probe. AI labels remain pending human review.

Uses the running API, never uploads/deletes documents or marks labels approved.
The first run freezes evidence before searching. Subsequent runs verify its hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


# Questions selected from source chunks before observing any search results.
# Each evidence group is a required supporting passage, not exhaustive relevance.
SPECS = [
    ("Vue3快速上手.md", 2, "Vue 3 的响应式底层相较旧版本改用了什么机制？"),
    ("Vue3快速上手.md", 5, "教程建议新建 Vue 项目采用什么工具，原有 CLI 处于什么状态？"),
    ("FTP实验指导win11备用版.pdf", 2, "在 Windows IIS 管理器中，从哪里添加一个 FTP 站点？"),
    ("FTP实验指导win11备用版.pdf", 5, "按照实验指导，供 FTP 下载的文件应该放到哪个本地目录？"),
    ("8.数字水印概述.pdf", 5, "最早带水印的纸张出现于哪一年、哪个地方？当时为什么使用水印？"),
    ("7.隐写技术（下）.pdf", 5, "图像信息隐藏中，把空域转换到频域常用哪三种变换？"),
    ("6.隐写技术（上）.pdf", 5, "把载体表示为序列时，二值图像和灰度图像的元素分别可以取哪些值？"),
    ("5.隐写术基本原理.pdf", 2, "Steganography 要把秘密放在哪里，其通信目标是什么？"),
    ("5.隐写术基本原理.pdf", 5, "隐秘通信中的囚徒模型由谁提出，Wendy 在模型中负责什么？"),
    ("3.数字图像处理基础（中）.pdf", 2, "几何点操作和灰度点操作分别修改图像的什么属性？"),
    ("3.数字图像处理基础（中）.pdf", 2, "两幅图像进行逐像素算术或逻辑运算时，对空间分辨率有什么要求？"),
    ("3.数字图像处理基础（中）.pdf", 5, "图像求反会怎样改变黑白关系？"),
    ("3.3 卷积和.pdf", 5, "计算离散卷积和时，求和变量和最终结果的自变量分别是什么？"),
    ("3.2 单位序列响应和阶跃响应.pdf", 5, "离散 LTI 系统的阶跃响应是什么响应，可以用哪两类方法求解？"),
    ("2.数字图像处理基础（上）.pdf", 2, "与模拟图像相比，数字图像在精度、处理和重复性方面有哪些优点？"),
    ("2.数字图像处理基础（上）.pdf", 5, "人眼适应亮光和暗光大约各需要多长时间，哪一种更快？"),
    ("2.5 相关函数.pdf", 5, "用图解法计算相关函数和卷积积分，是否都需要反转信号？"),
    ("2.4 卷积积分的性质.pdf", 2, "多个子系统并联时，总系统的冲激响应如何由各分支得到？"),
    ("2.2 冲激响应和阶跃响应(1).pdf", 2, "用微分方程求连续 LTI 系统冲激响应时，怎样求零时刻右侧初始值？"),
    ("2.1 LTI连续系统的响应.pdf", 2, "系统固有响应的函数形式取决于系统特性还是外部激励的形式？"),
    ("1.信息隐藏绪论.pdf", 5, "信息隐藏与加密技术分别保护什么，载体或密文能否直接使用？"),
    ("1.4 系统的分类和特性.pdf", 2, "为什么带有电容、电感的系统通常属于记忆系统？"),
    ("1.3 阶跃函数和冲激函数.pdf", 5, "狄拉克冲激适合描述什么物理量，其积分面积是多少？"),
    ("1.2 信号的基本运算.pdf", 5, "对有跳变的信号求导时，如何表示间断点处的导数及其强度？"),
]

# Indices refer to the source evidence above. Both groups must be retrieved.
MULTI = [
    (13, 18, "分别依据离散系统和连续系统的课件，说明求阶跃响应可用的方法，以及连续系统冲激响应初始值的求法。"),
    (16, 17, "结合相关函数和卷积性质课件，说明相关运算是否需要反转，以及并联系统的总冲激响应如何计算。"),
    (7, 20, "结合隐写术原理与信息隐藏绪论，说明隐秘通信的目标，以及它与加密在保护对象上的区别。"),
    (9, 14, "结合数字图像基础上、中的课件，说明数字图像相对模拟图像的优势，以及几何点操作修改的属性。"),
    (22, 23, "结合冲激函数定义和信号微分课件，解释冲激的积分面积，以及信号跳变处导数中冲激的强度如何确定。"),
    (5, 6, "结合隐写技术上、下两份课件，列出灰度载体元素的取值范围与三种常用空域到频域变换。"),
]


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def request(api, route, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(api + route, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=120) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="eval/reports/resume_evidence_20260919")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    docs = [request(args.api, "/api/documents/" + d["id"])
            for d in request(args.api, "/api/documents")]
    real = [d for d in docs if d["file_type"] == "pdf" or d["title"] in
            {"Vue3快速上手.md", "深度研究报告.md"}]
    normalized = sorted([{"id": d["id"], "title": d["title"], "status": d["status"],
                          "chunks": sorted(d["chunks"], key=lambda c: c["chunk_index"])}
                         for d in real], key=lambda d: d["id"])
    corpus_hash = digest(normalized)
    if any(d["status"] != "completed" for d in real):
        raise RuntimeError("Selected corpus includes incomplete documents")
    mismatches = [d["id"] for d in docs if d["chunk_count"] != len(d["chunks"])]
    if mismatches:
        raise RuntimeError(f"Stored and actual chunk counts disagree: {mismatches}")
    inventory = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "all_document_count": len(docs), "all_chunk_count": sum(len(d["chunks"]) for d in docs),
        "status_counts": dict(Counter(d["status"] for d in docs)),
        "selected_document_count": len(real), "selected_chunk_count": sum(len(d["chunks"]) for d in real),
        "selected_file_types": dict(Counter(d["file_type"] for d in real)),
        "corpus_sha256": corpus_hash,
        "selection": "All existing PDFs plus Vue3快速上手.md and 深度研究报告.md; excludes generated IFBENCH corpus and product/architecture fixtures",
        "documents": [{"id": d["id"], "title": d["title"], "status": d["status"],
                       "chunks": len(d["chunks"]),
                       "cid_placeholder_chunks": sum("(cid:" in c["content"] for c in d["chunks"])} for d in real],
    }
    dump(out / "inventory.json", inventory)
    dump(out / "corpus_snapshot.json", {"captured_at": inventory["captured_at"], "documents": docs})
    labels_path = out / "questions.pending.json"
    if labels_path.exists():
        dataset = json.loads(labels_path.read_text(encoding="utf-8"))
        if dataset["corpus_sha256"] != corpus_hash:
            raise RuntimeError("Corpus changed: use a fresh output directory and review evidence")
    else:
        cases = []
        for i, (title, index, query) in enumerate(SPECS):
            doc, = [d for d in real if d["title"] == title]
            chunk, = [c for c in doc["chunks"] if c["chunk_index"] == index]
            cases.append({"id": f"q{i+1:02}", "query": query, "type": "single_evidence",
                          "label_status": "ai_draft_pending_human_review", "evidence_groups": [{
                              "document_id": doc["id"], "title": title, "chunk_ids": [chunk["id"]],
                              "chunk_index": index, "page_number": chunk.get("page_number"),
                              "evidence_text": chunk["content"]}]})
        for a, b, query in MULTI:
            cases.append({"id": f"q{len(cases)+1:02}", "query": query, "type": "multi_evidence",
                          "label_status": "ai_draft_pending_human_review",
                          "evidence_groups": cases[a]["evidence_groups"] + cases[b]["evidence_groups"]})
        dataset = {"purpose": "exploratory_real_corpus_probe_not_formal_evaluation",
                   "created_at": datetime.now(timezone.utc).isoformat(), "corpus_sha256": corpus_hash,
                   "label_author": "AI", "human_reviewed": False, "cases": cases}
        dump(labels_path, dataset)
    review = ["# 问题与证据复核表", "", "AI 初标，未经过人工审核；每题可补充等价证据 Chunk，不是穷尽相关性标签。", ""]
    for case in dataset["cases"]:
        review += [f"## {case['id']}：{case['query']}", "", "复核：待确认（问题可回答性、证据充分性、等价段落）", ""]
        for group in case["evidence_groups"]:
            review += [f"来源：{group['title']}；Chunk {group['chunk_index']}；ID {group['chunk_ids'][0]}", "", group["evidence_text"], ""]
    (out / "label_review.md").write_text("\n".join(review), encoding="utf-8")
    rng = random.Random(20260919)
    rows = []
    # Fix scope and K for all arms; rotate mode order to reduce ordering bias.
    with (out / "cases.jsonl").open("w", encoding="utf-8") as handle:
        for case in dataset["cases"]:
            modes = ["bm25", "vector", "hybrid"]
            rng.shuffle(modes)
            for mode in modes:
                started = time.perf_counter()
                error = None
                result = None
                try:
                    result = request(args.api, "/api/search", {"query": case["query"], "mode": mode,
                                     "top_k": 10, "threshold": 0.0, "document_ids": [d["id"] for d in real]})
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                ids = {r["chunk_id"] for r in (result or {}).get("results", [])}
                hits = [bool(ids.intersection(g["chunk_ids"])) for g in case["evidence_groups"]]
                row = {"case_id": case["id"], "mode": mode, "query": case["query"], "type": case["type"],
                       "latency_ms": (time.perf_counter()-started)*1000, "error": error,
                       "evidence_hit_at_10": any(hits) if not error else False,
                       "all_evidence_at_10": all(hits) if not error else False,
                       "evidence_group_coverage_at_10": sum(hits)/len(hits) if not error else 0,
                       "result": result}
                rows.append(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
            print(f"Completed {case['id']}/{len(dataset['cases'])}", flush=True)
    # Reject a run whose selected source content changed while requests ran.
    after = []
    for d in real:
        current = request(args.api, "/api/documents/" + d["id"])
        after.append({"id": current["id"], "title": current["title"], "status": current["status"],
                      "chunks": sorted(current["chunks"], key=lambda c: c["chunk_index"])})
    unchanged = digest(sorted(after, key=lambda d: d["id"])) == corpus_hash
    summary = {"status": "exploratory_pending_human_review", "corpus_unchanged": unchanged,
               "corpus_sha256": corpus_hash, "dataset_sha256": digest(dataset), "top_k": 10,
               "question_count": len(dataset["cases"]), "request_count": len(rows), "metrics": {}}
    for mode in ["bm25", "vector", "hybrid"]:
        group = [r for r in rows if r["mode"] == mode]
        single = [r for r in group if r["type"] == "single_evidence"]
        multi = [r for r in group if r["type"] == "multi_evidence"]
        summary["metrics"][mode] = {
            "errors": sum(bool(r["error"]) for r in group),
            "single_evidence_hits": sum(r["evidence_hit_at_10"] for r in single), "single_count": len(single),
            "multi_all_evidence_hits": sum(r["all_evidence_at_10"] for r in multi), "multi_count": len(multi),
            "all_question_complete_hits": sum(r["all_evidence_at_10"] for r in group), "count": len(group)}
    dump(out / "summary.json", summary)
    failures = [r for r in rows if r["error"] or not r["all_evidence_at_10"]]
    dump(out / "misses.json", failures)
    report = ["# 真实文档检索探索性评测", "", "**AI 初标、待人工复核；不是正式检索效果，不能直接写成已验证的提升。**", "",
              f"语料：{len(real)} 份真实文档，{inventory['selected_chunk_count']} 个实际 Chunk。全库 {len(docs)} 份、{inventory['all_chunk_count']} 个 Chunk。",
              "", "24 道单证据题 + 6 道双文档题；固定同一语料范围、Top-10、原有线上 API 配置，三种模式共 90 次请求。",
              "", "| 模式 | 单证据命中 | 双文档全部证据命中 | 全部题完整命中 | 请求错误 |", "|---|---:|---:|---:|---:|"]
    for mode, m in summary["metrics"].items():
        report.append(f"| {mode} | {m['single_evidence_hits']}/24 | {m['multi_all_evidence_hits']}/6 | {m['all_question_complete_hits']}/30 | {m['errors']} |")
    report += ["", "## 口径与限制", "",
               "- 衡量预先指定证据组是否被检索到；未穷尽所有等价相关段落，因此不称为 Recall@10，也不等同于答案准确率。",
               "- 问题来自原文的 AI 初标，样本小、未覆盖所有文档、未经过用户独立审核；不能作为业务效果或模型泛化结论。",
               "- 所有模式使用相同文档范围与最终 K；产品 API 在显式多文档范围下启用候选配额，Vector 有最低证据阈值，Hybrid 包含 RRF 与多样性策略。比较的是现有产品路径，不是仅改变 RRF 的纯算法消融。",
               f"- 当前选定语料中含 CID 占位符的片段数：{sum(d['cid_placeholder_chunks'] for d in inventory['documents'])}；零占位符不代表 OCR 内容完全准确。",
               f"- 运行前后语料内容一致：{unchanged}。完整请求结果见 cases.jsonl，未命中案例见 misses.json。",
               "", "## 目前可用于简历的规模表述", "",
               f"在 {len(real)} 份 PDF/Markdown 文档、{inventory['selected_chunk_count']:,} 个 Chunk 的本地知识库上验证文档入库与检索流程，并建立 30 题检索对照评测草案。",
               "", "检索提升数字需先审核 label_review.md 中的问题和证据、补齐等价段落后重新运行。"]
    (out / "report.md").write_text("\n".join(report)+"\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if not unchanged or any(r["error"] for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
