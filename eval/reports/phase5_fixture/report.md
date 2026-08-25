# 检索消融评测报告

> **合成 Fixture，仅验证公式和报告管线；以下数字不是 InsightFlow 项目效果。**

- 状态：`synthetic_fixture_completed`
- 数据集：`synthetic-metric-fixture@1.0.0`
- 数据 SHA-256：`850259eec9c85aa4d3c447ed115068ca030ba76ec12977ad94c9f16b1111baf3`
- Top-K：`5`
- 随机种子：`20260813`
- Embedding：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- LLM：`deepseek-chat`

## 三方案同条件结果

| 方案 | Recall@5 | MRR | nDCG@5 | 引用准确性 | 忠实度 | 平均延迟(ms) | P50 | P95 | 平均 Token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bm25 | 1.0000 | 0.7500 | 0.8467 | 不可用 | 不可用 | 0.0042 | 0.0042 | 0.0067 | 不可用 |
| vector | 1.0000 | 0.7500 | 0.7753 | 不可用 | 不可用 | 0.0017 | 0.0017 | 0.0023 | 不可用 |
| hybrid | 1.0000 | 1.0000 | 1.0000 | 不可用 | 不可用 | 0.0012 | 0.0012 | 0.0016 | 不可用 |

## 失败与错误分类

无。

## 指标口径

- Recall@5：前 5 个结果中的唯一相关 Chunk 数 / Gold 相关 Chunk 总数。
- MRR：首个相关 Chunk 排名的倒数；无命中为 0。
- nDCG@5：二元相关性的 DCG@5 / 理想 DCG@5。
- 引用准确性：答案中引用位置映射到 Gold 相关 Chunk 的数量 / 全部引用编号数量。没有生成答案、引用或 Gold 时不可用。
- 回答忠实度：仅在显式启用 LLM Judge 时可用；Judge 原始 prompt、模型、温度、时间、原始输出和解析结果保存在逐题 JSONL。
- 延迟：同一进程、同一数据与 Top-K 下的实测检索 wall-clock；汇总平均值及线性插值 P50/P95。
- `summary.json` 另分别记录检索、生成和端到端总延迟的 count、平均、P50、P95；未启用生成时生成延迟为空。Markdown/CSV 主表展示检索延迟，保证三种检索方案可比。
- Token：仅读取生成 API 返回的 usage；缺失或为零时标记不可用，不做估算。
