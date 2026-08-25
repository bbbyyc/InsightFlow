# InsightFlow 检索评测

统一入口为 `python eval/run_eval.py`。三种方案固定为 `bm25`、`vector`、`hybrid`，在同一进程、同一数据集、同一冻结语料、同一 Top-K、同一文档过滤与随机种子下依次评测。Hybrid 的两路候选池为 `Top-K × candidate_multiplier`，最终输出仍严格为同一 Top-K；这是 RRF 所需候选参数，会写入报告。

## 数据规范与人工审核

项目数据集每题必须包含：`query`、`relevant_document_ids`、`relevant_chunk_ids`、`question_type`、`data_source`、`label_review_status`；可以包含 `reference_answer`、`key_facts`、`document_filter_ids`。正式运行还要求：

- `label_review_status` 为 `approved` 或 `approved_human`；
- Gold document/chunk ID 非空；
- `label_review.reviewer` 和 `reviewed_at` 非空；
- 数据集用途为 `project_evaluation`；
- 关联的 corpus manifest 为 `approved`，包含冻结版本、文件 SHA-256 和稳定数据库 document ID；
- manifest 中每个文档实际存在于当前数据库。空 `document_filter_ids` 会自动限制到 manifest 的全部文档，不会检索数据库中的其他现场数据。

当前需人工确认的文件是 `eval/datasets/candidate_project_v1.json` 和 `eval/corpus_manifest.pending.json`。不得使用模型自动生成并自动审核这些标签。

## 命令

正式命令（当前会因未审核标签而退出码 3 拒绝）：

```powershell
python eval/run_eval.py --dataset eval/datasets/candidate_project_v1.json --output-dir eval/reports/formal --top-k 5 --seed 20260813
```

合成公式/管线自测：

```powershell
python eval/run_eval.py --fixture --dataset eval/datasets/synthetic_fixture_v1.json --output-dir eval/reports/synthetic_fixture --top-k 5 --seed 20260813
```

只有人工 Gold、语料和数据库就绪后，才可在正式命令增加 `--include-generation`；再增加 `--llm-judge` 才会调用忠实度 Judge。Judge 的模型、完整 prompt、温度 0、请求/完成时间、原始输出、解析结果、错误和 API usage 均写入 `cases.jsonl`。Token 只读取真实 API usage，缺失时为 `null/unavailable`。

每次输出：`cases.jsonl`、`summary.json`、`comparison.csv`、`report.md`、`failures.json`。
