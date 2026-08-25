# InsightFlow Phase 2 结果

完成日期：2026-08-13（Asia/Shanghai）  
范围：评测数据规范、语料 manifest、指标、统一运行程序、报告生成、合成实验与测试；未开发或修改前端，未修改业务数据库结构、依赖或部署。

## 1. 结论

可复现检索消融评测框架已建立，但**没有生成正式项目效果数字**。

全仓扫描只发现旧 `eval/test_dataset.json` 的 5 个 `expected_doc: test_doc.md` 标签和 2026-07-15 的旧报告。它们没有稳定 document/chunk ID、人工审核状态、审核者、语料版本或 label provenance；旧报告也与当前旧脚本输出结构不一致。因此不存在可用于正式指标的可靠 Gold Label。

本阶段据此：

- 生成 7 条 `pending_review` 项目候选题，Gold ID 留空；
- 建立冻结语料 manifest 模板，状态为 `pending_review`；
- 正式命令默认并强制拒绝未审核数据；
- 建立 2 条手写合成 Fixture，只验证指标公式和五类报告产物；
- 实际执行合成管线，同时实际验证正式候选集被拒绝；
- 未调用 DeepSeek，未生成回答忠实度、引用准确性或 Token 数字；这些字段标记为不可用。

## 2. 实现文件

| 文件 | 内容 |
| --- | --- |
| `eval/dataset.py` | 数据 schema 校验、SHA-256、正式 Gold readiness 门禁。 |
| `eval/datasets/candidate_project_v1.json` | 7 条项目候选题，全部 `pending_review`，含规定字段与人工审核槽位。 |
| `eval/datasets/synthetic_fixture_v1.json` | 2 条手写合成题及三方案固定排名，仅验证公式/报告。 |
| `eval/corpus_manifest.pending.json` | 待人工冻结的语料版本、文件 hash、稳定 document/chunk provenance 要求。 |
| `eval/metrics.py` | Recall@5、MRR、nDCG@5、引用准确性、平均/P50/P95、可用值平均。 |
| `eval/run_eval.py` | 统一 BM25/vector/hybrid 同条件运行、Gold/语料门禁、真实服务/Fixture 路径、生成/Judge、五类产物。 |
| `eval/test_metrics.py` | 指标公式、无 Gold 返回不可用、引用映射和延迟百分位测试。 |
| `eval/test_dataset.py` | 候选集阻塞、Fixture 非正式数据、缺字段拒绝测试。 |
| `eval/README.md` | 数据审核流程、正式/Fixture 命令、Judge 和 Token 规则。 |
| `eval/reports/synthetic_fixture/*` | 合成管线实际产物；醒目标记非项目效果。 |
| `eval/reports/formal_blocked/*` | 正式命令被拒绝后的空指标报告和 28 条阻塞原因。 |
| `docs/UPGRADE_PLAN.md` | 更新 Phase 2 状态和正式实验前置条件。 |
| `docs/PHASE2_RESULT.md` | 本记录。 |

旧 `eval/test_dataset.json` 与 `eval/eval_report.json` 未删除，作为历史证据保留，但统一命令不再默认使用它们。

## 3. 同条件消融设计

正式程序固定三种模式：`bm25`、`vector`、`hybrid`。每题按相同顺序在同一进程运行，共享：

- 同一冻结 corpus manifest 和数据库语料；
- 同一数据集及 SHA-256；
- 同一问题；
- 同一最终 Top-K（默认 5）；
- 同一 `document_filter_ids`。若为空，则统一限制到 manifest 的全部冻结文档，避免检索现场数据库的额外数据；
- 同一 Python/OS/配置、Embedding 模型、随机种子；
- 同一输出与错误分类。

BM25 与 vector 直接取最终 Top-K。Hybrid 因 RRF 需要候选池，两路都取 `Top-K × candidate_multiplier`（默认 3），再融合为同一最终 Top-K；candidate multiplier 与 RRF `k=60` 都写入 metadata。这不是改变最终比较 K，而是 Hybrid 的公开算法参数。

正式运行还会校验：所有标签经人工批准、Gold ID 非空、审核者/时间存在、corpus manifest 为 `approved`、文件 hash/document ID 完整、manifest 文档在当前数据库实际存在。

## 4. 数据规范与待人工确认

每题包含：

- `query`
- `relevant_document_ids`
- `relevant_chunk_ids`
- 可选 `reference_answer`、`key_facts`
- `question_type`
- `data_source`
- `label_review_status`
- `label_review.reviewer/reviewed_at/notes`
- 可选且三方案共用的 `document_filter_ids`

必须人工确认：

1. `eval/corpus_manifest.pending.json`：选定不含敏感信息、许可明确的真实语料；写入 corpus version、文件 SHA-256、稳定数据库 document ID、title、来源和纳入理由；执行生产解析/切块后记录 chunk ID/content hash 与参数；将状态改为 `approved`。
2. `eval/datasets/candidate_project_v1.json`：针对冻结语料逐题填写全部相关 document/chunk ID，验证问题、参考事实和负证据；填写人工审核者与时间，把状态改为 `approved_human`。
3. 至少双人抽查跨文档综合题与“缺少证据”题，避免只标一个容易找到的 chunk 而漏标其他相关项。

不得由同一个模型生成标签、审核标签后据此宣称提升。本框架不会自动改写审核状态。

## 5. 指标公式与数据来源

### Recall@5

`|unique(retrieved_chunk_ids[:5]) ∩ Gold relevant_chunk_ids| / |Gold relevant_chunk_ids|`

来源：逐题人工审核的 `relevant_chunk_ids` 与实际检索排名。Gold 为空时返回 `null`，不将其视为 1。

### MRR

首个 Gold 相关 Chunk 位于排名 `r` 时为 `1/r`；Top-K 内无相关结果为 0。Gold 为空为 `null`。

### nDCG@5

使用二元相关性：`DCG@5 = Σ rel_i/log2(i+1)`；`nDCG@5 = DCG@5 / IDCG@5`，其中 IDCG 把最多 5 个 Gold 相关 Chunk 放在最前。Gold 为空为 `null`。

### 引用准确性

`答案中引用位置映射到 Gold 相关 Chunk 的数量 / 答案中全部唯一引用编号数量`。引用位置按送入生成模型的上下文顺序映射到 chunk ID；非法编号计入分母但不计正确。未生成答案、无引用或无 Gold 时为 `null`。

### 回答忠实度

仅显式 `--include-generation --llm-judge` 才执行。Judge 只依据源片段，以 1—5 评分。逐题 JSONL 保存：

- 实际 `DEEPSEEK_MODEL`；
- 完整 `JUDGE_PROMPT_TEMPLATE` 展开内容；
- 温度 0；
- UTC 请求/完成时间；
- 原始输出；
- JSON 解析结果；
- API usage 和错误。

本次未运行 Judge，因此忠实度为不可用；没有伪造分数。

### 延迟

使用 `time.perf_counter()` 测量 observed wall-clock。`summary.json` 分别记录检索、生成、端到端的 count、算术平均、线性插值 P50 和 P95；CSV/Markdown 主表展示检索延迟，使三种检索方案可比。Fixture 的微秒级数字只是 Python 管线开销，不是项目延迟。

### Token

只读取真实生成 API 返回的 `usage.prompt_tokens/completion_tokens/total_tokens`。缺字段或全为零时记录 `status=unavailable` 与 `null`，不估算。本次未调用 API，故不可用。

## 6. 统一命令与产物

正式命令：

```powershell
python eval/run_eval.py --dataset eval/datasets/candidate_project_v1.json --output-dir eval/reports/formal --top-k 5 --seed 20260813
```

当前实测按设计拒绝，退出码 3。人工审核、冻结语料和数据库就绪后，同一命令执行真实三方案消融。可选生成/Judge：

```powershell
python eval/run_eval.py --dataset <approved.json> --output-dir eval/reports/formal --top-k 5 --seed 20260813 --include-generation --llm-judge
```

每次输出：

- `cases.jsonl`：逐题/逐方案排名、Gold、指标、延迟、回答、引用、Token、Judge provenance、错误；
- `summary.json`：运行环境、数据/语料版本和 hash、参数、三方案汇总；
- `comparison.csv`：三方案同列对比；
- `report.md`：人读报告与公式；
- `failures.json`：失败 Case 和 `unreviewed_gold_labels`、`corpus_not_approved`、`environment_unavailable`、`runtime_error`、`no_relevant_chunk_in_top5`、`judge_failure` 等分类。

## 7. 实际运行结果

使用 Codex bundled Python 3.12.13：

```powershell
$env:PYTHONPATH = "eval;backend"
python -m unittest discover -s eval -p "test_*.py" -v
python -m unittest discover -s backend/tests -p "test_*.py" -v
python -m compileall -q eval backend/app backend/tests
```

结果：

- Phase 2 数据/指标测试：**7/7 通过**；
- Phase 1 检索回归：**9/9 通过**；
- Python compileall：通过。

合成管线：

```powershell
python eval/run_eval.py --fixture --dataset eval/datasets/synthetic_fixture_v1.json --output-dir eval/reports/synthetic_fixture --top-k 5 --seed 20260813
```

结果：退出码 0，生成全部五类产物。报告明确标注“不是 InsightFlow 项目效果”。其合成指标只证明公式/汇总/CSV/Markdown 管线工作，不在本结果中作为质量结论引用。

正式候选集：

```powershell
python eval/run_eval.py --dataset eval/datasets/candidate_project_v1.json --output-dir eval/reports/formal_blocked --top-k 5 --seed 20260813
```

结果：按设计退出码 3；`summary.json` 状态为 `blocked_unreviewed_labels`，三方案的 Recall/MRR/nDCG、引用、忠实度、延迟和 Token 全部 `null/不可用`；`failures.json` 记录 28 条标签/Gold/provenance 阻塞原因。

## 8. 未验证项和边界

- Docker daemon 仍不可用；没有 PostgreSQL/pgvector/模型运行环境，故未进行真实项目三方案实验。
- 当前没有 approved corpus 或人工 Gold，这是正式评测的首要阻塞项，不是代码错误。
- 未调用 DeepSeek，因此忠实度、生成延迟、引用准确性和 Token 未验证。
- 旧上传目录含重复和可能敏感的用户文件，不能未经选择直接冻结为评测语料。
- 当前数据库中的稳定 document/chunk ID 无法在 Docker 未运行时导出；人工冻结时必须以实际生产切块结果为准。
- 忠实度 Judge 仍有模型偏差；即使启用，也应保留人工抽查，不作为唯一效果门禁。
- 评测程序按顺序运行三种模式；同进程共享缓存可能带来顺序效应。metadata 记录固定顺序，后续真实实验可增加轮换/预热策略，但不得改变同条件约束。

Phase 2 到此停止，不进入 Phase 3。
