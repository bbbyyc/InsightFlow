# InsightFlow Phase 1 结果

完成日期：2026-08-13（Asia/Shanghai）  
范围：仅修改检索链路、结果元数据、引用映射辅助代码、相关 API 响应和后端测试；未修改前端、评测页面、数据库结构、依赖或部署配置。

## 1. 完成结论

Phase 1 已完成。显式选择文档和未指定范围现在走不同、明确的策略：

- **未指定文档范围**：在全库运行原有 pgvector + BM25 + RRF 全局混合检索，不启用覆盖或文档配额。
- **明确指定一个文档**：只在该文档内全局排序，不启用多文档覆盖配额。
- **明确指定多个文档**：vector 和 BM25 都按文档限制候选召回配额；合并后按稳定 `chunk_id` 去重，再进行 RRF 和软多样性选择。
- 软多样性用于缓解一个文档挤占 Top-K，但不会保证每个指定文档都有结果，也不会追加低相关 chunk。
- 显式范围内若某文档没有 BM25 正分候选，且 vector similarity 低于证据阈值 `0.2`（或请求的更高 threshold），该文档被记录为缺少证据；chat/agent 返回“未检索到足够证据：<document_id>”，不调用 LLM 强答。

## 2. 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `backend/app/services/retrieval_service.py` | pgvector 查询支持显式范围内的 `per_document_limit`，用 `ROW_NUMBER() PARTITION BY document_id` 分配候选配额；排序增加稳定 Chunk ID tie-break。 |
| `backend/app/services/bm25_service.py` | BM25 支持显式范围内的每文档候选上限，仍保留过滤集合和原始分数。 |
| `backend/app/services/hybrid_service.py` | 重写检索契约：稳定去重、通道元数据、RRF 排名、显式范围配额、软多样性、证据阈值与缺失文档诊断。 |
| `backend/app/services/citation_service.py` | 新增纯函数引用映射，按不可变上下文序号映射 Chunk ID，并忽略非法编号/重复 Chunk。 |
| `backend/app/services/rag_service.py` | 引用提取复用统一纯函数；生成和 rerank 行为未改。 |
| `backend/app/routers/search.py` | 搜索结果新增召回通道、通道原始排名/分数、RRF 排名、最终融合排名和 diagnostics；单通道搜索也保留统一元数据。 |
| `backend/app/routers/chat.py` | 未指定范围时执行全局 hybrid；删除 rerank 后逐文档强制补 top-1；返回 retrieval diagnostics；证据不足时不调用 LLM。 |
| `backend/app/services/agent_service.py` | Agent 接入统一 diagnostics；显式范围证据不足时停止强答并返回结构化结果。 |
| `backend/app/routers/agent.py` | Agent 响应暴露 retrieval diagnostics。 |
| `backend/app/mcp_server.py` | 搜索输出补充 chunk/document ID、召回通道、通道排名和融合排名。 |
| `backend/tests/test_core.py` | 重构为标准库 `unittest` 可运行的 9 个离线检索契约/回归测试。 |
| `docs/UPGRADE_PLAN.md` | 将 Phase 1 标记完成并记录动态验证边界。 |
| `docs/PHASE1_RESULT.md` | 本结果记录。 |

## 3. 算法流程对比

### 修改前

1. 有多个 `document_ids` 时，只有 vector 按文档循环召回，BM25 在整个过滤集合中排名。
2. vector 与 BM25 直接 RRF，结果只保留统一 `score`，没有通道、原始通道排名或融合排名。
3. chat rerank 截断后检查每个指定文档；缺席文档无条件执行 vector top-1 并追加。
4. 追加结果不再 rerank，可能超过 `top_k`，低相关内容也会为了覆盖进入上下文。
5. chat 没有 `document_ids` 时不检索任何知识库内容。

### 修改后

1. 判定范围：空 `document_ids` 为全局；非空为显式范围。会话已保存的文档也视为用户已明确选择的范围。
2. 全局模式：vector 与 BM25 在全库召回，保持原有 RRF Top-K，不应用文档配额/覆盖。
3. 显式多文档模式：两通道均以 `ceil(channel_top_k / document_count)`、最低 2 个为每文档候选上限。
4. RRF 按 `chunk_id` 稳定去重；同一通道重复项只计第一次排名。并列时用 Chunk ID 稳定排序。
5. 每项保留：`document_id`、`section_title`、`chunk_id`、`retrieval_channels`、`channel_ranks`、`channel_scores`、`rrf_rank`、`fused_rank`。
6. 显式范围证据过滤：BM25 正分命中或 vector similarity 达到 `max(request_threshold, 0.2)` 才算候选证据。
7. 多文档软多样性：对自然 Top-K 分数 90% 范围内的候选施加单文档约 60% 的软上限；若没有足够相近候选，则按原 RRF 顺序回填，因此单文档仍可占多数或全部结果。
8. diagnostics 区分 `documents_with_evidence`、`documents_in_results`、`documents_below_fused_cutoff` 和 `missing_documents`。有证据但未进 Top-K 不会被误判为缺失，也不会被补入。
9. 显式文档缺少证据时 chat/agent 返回“未检索到足够证据”，引用为空、token usage 为 0；不调用 DeepSeek 强答。

## 4. 实际测试

运行环境：Codex bundled Python 3.12.13。项目依赖未安装，测试刻意使用纯 Python fake retriever/BM25 验证检索契约，不访问网络、DeepSeek、PostgreSQL 或 Redis。

执行命令：

```powershell
$env:PYTHONPATH = "backend"
& "C:\Users\28891\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" `
  -m unittest discover -s backend/tests -p "test_*.py" -v
```

最终结果：**9 tests，全部通过**。

覆盖项：

1. 单文档问答范围过滤：通过。
2. 明确指定多个文档及两通道召回配额：通过。
3. 未指定文档范围继续全局 hybrid：通过。
4. 指定文档缺少相关证据、记录原因并生成固定提示：通过。
5. 单一高相似文档挤占候选时的软多样性：通过。
6. 弱相关其他文档不因覆盖被提升：通过。
7. 重复 Chunk 跨通道合并和元数据：通过。
8. 引用编号与原始 Chunk 映射、非法编号忽略：通过。
9. 原有 RRF 共享命中排首位回归：通过。

另执行：

```powershell
python -m compileall -q backend/app backend/tests
```

使用上述 bundled Python 执行，所有后端应用和测试 Python 文件编译成功。

首次测试为 8/9，通过后发现 Top-3 的软上限取整仍允许单文档占满；将软上限从 70% 调整为 60% 后重新执行完整套件，最终 9/9 通过。

## 5. 适用条件

- 覆盖/多样性策略仅在 `document_ids` 非空且包含多个唯一文档时启用。
- 单文档显式范围只做过滤和证据判断，不做跨文档多样性。
- 空范围不限制全库，继续原有 vector + BM25 + RRF。
- `top_k` 是最终硬上限；不会因文档数增加而扩大上下文。
- 缺失证据判断只用于显式范围；全局检索不会因为某个未选择文档缺席而报错。
- BM25 的正分命中视为词法证据；vector 默认最低 similarity 0.2，可由请求更高 threshold 收紧。

## 6. 已知边界与未验证项

- Docker daemon 仍不可用，本次未能执行真实 PostgreSQL/pgvector SQL、FastAPI HTTP、Celery、MCP 或容器 E2E。`ROW_NUMBER()` 配额 SQL 已通过 Python 语法检查，但尚缺真实 pgvector 集成测试。
- 宿主/bundled Python 没有 FastAPI、SQLAlchemy、jieba、rank-bm25、pytest 等项目依赖；按本阶段范围未安装或修改依赖。因此测试验证的是排序与诊断契约，不是数据库/框架集成。
- 固定 vector evidence threshold `0.2` 是保守工程默认值，尚未由可复现语料校准；应在 Phase 2 用数据确定。
- RRF 只使用通道排名，不直接使用 vector/BM25 原始分值；原始分数被保留用于诊断和证据门槛。
- LLM rerank 可能改变最终上下文顺序。当前 metadata 的 `fused_rank` 表示 RRF/多样性后的排名，不表示 LLM rerank 后排名；Phase 2 应决定是否新增独立 `rerank_rank`。
- search 单通道 API 有统一 metadata，但 BM25/pgvector 的真实过滤与配额仍需数据库集成测试。
- Phase 1 未修改前端类型，因此前端目前不会展示新 diagnostics/元数据；这属于 Phase 4，不在本阶段范围。

Phase 1 到此停止，不进入 Phase 2。
