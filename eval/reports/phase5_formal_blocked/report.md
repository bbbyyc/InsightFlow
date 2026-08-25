# 检索消融评测报告

> **评测被阻止，未生成项目效果数字。**

- 状态：`blocked_unreviewed_labels`
- 数据集：`insightflow-project-candidate@1.0.0-candidate`
- 数据 SHA-256：`240ad2a73abcea7fcb699d5f25c0e4b1fd6bcd7a8c43a7aa6d2afd47e5818103`
- Top-K：`5`
- 随机种子：`20260813`
- Embedding：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- LLM：`deepseek-chat`

## 三方案同条件结果

| 方案 | Recall@5 | MRR | nDCG@5 | 引用准确性 | 忠实度 | 平均延迟(ms) | P50 | P95 | 平均 Token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bm25 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 |
| vector | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 |
| hybrid | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 |

## 失败与错误分类

- `run` / `all`：unreviewed_gold_labels — candidate-q1: label_review_status=pending_review
- `run` / `all`：unreviewed_gold_labels — candidate-q1: relevant_document_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q1: relevant_chunk_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q1: human review provenance is incomplete
- `run` / `all`：unreviewed_gold_labels — candidate-q2: label_review_status=pending_review
- `run` / `all`：unreviewed_gold_labels — candidate-q2: relevant_document_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q2: relevant_chunk_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q2: human review provenance is incomplete
- `run` / `all`：unreviewed_gold_labels — candidate-q3: label_review_status=pending_review
- `run` / `all`：unreviewed_gold_labels — candidate-q3: relevant_document_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q3: relevant_chunk_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q3: human review provenance is incomplete
- `run` / `all`：unreviewed_gold_labels — candidate-q4: label_review_status=pending_review
- `run` / `all`：unreviewed_gold_labels — candidate-q4: relevant_document_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q4: relevant_chunk_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q4: human review provenance is incomplete
- `run` / `all`：unreviewed_gold_labels — candidate-q5: label_review_status=pending_review
- `run` / `all`：unreviewed_gold_labels — candidate-q5: relevant_document_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q5: relevant_chunk_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q5: human review provenance is incomplete
- `run` / `all`：unreviewed_gold_labels — candidate-q6: label_review_status=pending_review
- `run` / `all`：unreviewed_gold_labels — candidate-q6: relevant_document_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q6: relevant_chunk_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q6: human review provenance is incomplete
- `run` / `all`：unreviewed_gold_labels — candidate-q7: label_review_status=pending_review
- `run` / `all`：unreviewed_gold_labels — candidate-q7: relevant_document_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q7: relevant_chunk_ids is empty
- `run` / `all`：unreviewed_gold_labels — candidate-q7: human review provenance is incomplete

## 指标口径

- Recall@5：前 5 个结果中的唯一相关 Chunk 数 / Gold 相关 Chunk 总数。
- MRR：首个相关 Chunk 排名的倒数；无命中为 0。
- nDCG@5：二元相关性的 DCG@5 / 理想 DCG@5。
- 引用准确性：答案中引用位置映射到 Gold 相关 Chunk 的数量 / 全部引用编号数量。没有生成答案、引用或 Gold 时不可用。
- 回答忠实度：仅在显式启用 LLM Judge 时可用；Judge 原始 prompt、模型、温度、时间、原始输出和解析结果保存在逐题 JSONL。
- 延迟：同一进程、同一数据与 Top-K 下的实测检索 wall-clock；汇总平均值及线性插值 P50/P95。
- `summary.json` 另分别记录检索、生成和端到端总延迟的 count、平均、P50、P95；未启用生成时生成延迟为空。Markdown/CSV 主表展示检索延迟，保证三种检索方案可比。
- Token：仅读取生成 API 返回的 usage；缺失或为零时标记不可用，不做估算。
