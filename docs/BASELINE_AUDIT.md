# InsightFlow Phase 0 基线审计

审计日期：2026-08-13（Asia/Shanghai）  
审计范围：当前工作区静态代码、配置、现有数据/构建产物，以及在本机可执行的测试、构建与启动检查。Phase 0 未修改业务代码、数据库结构或依赖。

## 1. 结论摘要

当前项目是一个有实质实现的原型，而非纯 Mock：FastAPI、SQLAlchemy/pgvector、BM25、RRF、DeepSeek RAG、LangGraph、Celery、Next.js 页面和 Compose 编排均存在真实调用代码。但它还不能被认定为“可完整启动、可复现验收”的交付版本：Docker 引擎未运行且无法由当前会话启动，宿主 Python 环境没有项目依赖，DeepSeek 与真实数据库链路因此未完成本次动态验证。

状态定义：

1. **已真实实现**：存在完整调用代码，且本次有可运行证据或无需外部设施的针对性测试。
2. **部分实现**：存在实质代码，但正确性、完整性或工程闭环不足。
3. **接口/占位/Mock**：只有接口、样例或伪实现。
4. **完全缺失**：扫描未发现实现。
5. **环境/密钥无法验证**：代码存在，但本次环境无法完成动态验证。

核心判断：

| 能力 | 状态 | 事实依据 |
| --- | --- | --- |
| 文档上传、解析、切块、向量化 | 5 | `backend/app/routers/documents.py` 保存文件并投递 Celery；`backend/app/services/document_service.py:37-97` 执行解析、切块、向量化；本次无运行中的 PostgreSQL/Redis/worker。 |
| PostgreSQL ORM 与 pgvector | 5 | `backend/app/models/*.py` 定义 5 张表；`backend/app/models/chunk.py` 使用 `Vector(384)`；数据库未启动。 |
| 数据库迁移 | 4 | 依赖含 Alembic，但全仓扫描无 `alembic.ini`、迁移环境或 versions；`backend/app/main.py:13-24` 用 `create_all` 和裸 `ALTER/CREATE INDEX` 代替迁移。 |
| FastAPI REST API | 5 | `backend/app/main.py` 注册 documents/search/chat/agent；路由含真实 DB/服务调用；后端未能启动验证 OpenAPI/HTTP。 |
| BM25 | 2 | `backend/app/services/bm25_service.py:39-124` 从 DB 构建 jieba+BM25Okapi 内存索引并支持文档过滤；每个请求新建服务，索引不共享，rebuild 端点没有跨进程持久效果。 |
| pgvector 检索 | 5 | `backend/app/services/retrieval_service.py:14-65` 使用 `<=>` cosine distance、阈值、文档过滤；未连真实 DB 执行。 |
| RRF 融合 | 1 | `backend/app/services/hybrid_service.py:7-42` 实现标准 reciprocal-rank 累加；`backend/tests/test_core.py` 有双路命中排序测试。 |
| 跨文档检索 | 2 | `hybrid_service.py:61-79` 对多文档逐文档取向量候选；`routers/chat.py` 又为缺席文档强制追加 top-1。该策略保证覆盖，却破坏统一全局排名/最终 top_k，且没有跨文档测试。 |
| 引用映射 | 2 | `rag_service.py:35-43,116-139` 按上下文序号映射 chunk，并有稀疏编号单测；只做字符串 `[N]` 检测，不验证主张支持度，重复 chunk 去重后编号语义也缺少端到端验证。 |
| LangGraph 工作流 | 5 | `agent_service.py:29-45` 有 plan→search→条件循环→generate 图；依赖模型、embedding、DB，未动态执行。 |
| Redis/Celery 异步任务 | 5 | `backend/app/tasks.py` 定义 Redis broker/backend 和文档处理任务；上传端调用 `.delay()`；Redis/worker 未运行。 |
| 前端产品页面 | 2 | `frontend/app/page.tsx` 实现会话、文档、检索、研究模式、引用侧栏、上传与主题交互；`frontend/lib/api.ts` 对接真实 API。生产构建成功，但未做浏览器/API 联调。 |
| 评测指标代码 | 1 | `eval/metrics.py` 实现 Recall@K、MRR、nDCG、引用编号精度；`eval/test_metrics.py` 有单测，但本机缺 pytest，未执行。 |
| 可复现评测 | 2 | `eval/run_eval.py` 跑 bm25/vector/hybrid 并使用 LLM judge；数据集仅 5 个项目自描述问题，依赖预先存在且标题为 `test_doc.md` 的 DB 数据、外部模型和未固定响应。 |
| 自动化测试 | 2 | 仅 4 个单元测试：RRF 1、引用 1、指标 2；无 API、DB、Celery、LangGraph、前端或 E2E 测试。 |
| Docker Compose/启动 | 5 | `docker-compose.yml` 编排 postgres/redis/backend/worker/mcp/frontend；`docker compose config --quiet` 成功；Docker daemon 不可用，完整启动未验证。 |
| MCP | 5 | `backend/app/mcp_server.py` 的 search/ask/list 工具调用真实服务；Compose 启动 `app.run_mcp`，未动态验证 SSE。 |

## 2. 项目与依赖

- 后端固定 Python 依赖见 `backend/requirements.txt`：FastAPI 0.115.2、SQLAlchemy 2.0.35、pgvector 0.3.5、Celery 5.4.0、LangGraph 0.2.0、FastEmbed 0.4.2 等。
- 前端依赖见 `frontend/package.json` 与锁文件：Next.js 实际构建版本 14.2.35、React 18、TypeScript、Tailwind。
- Dockerfile 固定 Python/Node 基础镜像 digest；Compose 的 Postgres 密码 `insightflow123` 明文硬编码于本地开发配置（`docker-compose.yml`），不适用于生产。
- README 当前内容在默认 PowerShell 读取时乱码；以 UTF-8 读取代码正常。README 描述的主要能力与代码大体对应，但“完整启动”和评测结果不能由说明文字替代运行证据。
- 工作区 `.git` 目录存在但为空/不可用；系统 Git 与 bundled Git 均报告 `not a git repository`。因此本次**无法判断哪些文件已跟踪、哪些改动未提交**。

## 3. 配置与敏感信息

配置入口为 `backend/app/config.py`，示例为 `backend/.env.example`，Compose 注入 DB/Redis 容器地址。实际 `backend/.env` 存在，并包含 `DEEPSEEK_API_KEY` 等变量；本审计只读取变量名并遮蔽值。

- `.gitignore` 排除 `.env`，但因 Git 元数据不可用，不能证明实际 `.env` 从未提交。
- 静态扫描（排除实际 `.env`、上传内容、node_modules、`.next`）未发现形如真实 `sk-...` 的硬编码密钥；只发现 `.env.example` 的占位值。
- `docker-compose.yml` 硬编码开发数据库用户名/密码和公开端口 5432、6379。
- CORS 仅允许本机 3000（`backend/app/main.py`），没有按环境配置。
- `Chunk.embedding` 固定为 384 维，而配置允许修改 `EMBEDDING_DIMENSIONS`；改变配置会与模型/表结构不一致。

## 4. 数据库、ORM 与持久化

`Document`、`Chunk`、`Conversation`、`Message`、`ConversationDocument` 均是真实 SQLAlchemy 模型；会话、消息、引用 JSON 和会话文档关联均写数据库。主要缺口：

- 没有 Alembic 迁移基线、升级/回滚路径或 schema version。
- 应用启动时执行 DDL；部署账号必须有扩展和建表权限。
- HNSW 索引无维度迁移策略；`embedding_model` 是启动时裸 ALTER 增补。
- `ConversationDocument` 没有 `(conversation_id, document_id)` 唯一约束，应用层的先查后插存在并发重复风险。
- 删除文档只删 DB 实体，`backend/app/routers/documents.py` 未删除磁盘上传文件。
- 工作区存在 `backend/data/uploads` 与 `data/uploads` 的真实文件；由于 Git 不可用，无法确认是否未提交或是否为用户数据。

## 5. API 与检索链路

实际路由：健康检查；文档上传/列表/详情/删除；vector/bm25/hybrid 搜索与 rebuild-index；聊天及会话 CRUD/文档关联；Agent。代码依据分别在 `backend/app/main.py` 与 `backend/app/routers/*.py`。

检索链路为：FastEmbed 查询向量 → pgvector cosine SQL；并行逻辑上的 jieba/BM25 → RRF(`k=60`) → 可选 DeepSeek rerank → prompt 上下文 → DeepSeek 回答 → `[N]` 到 chunk 映射。

已确认的问题：

1. 多文档时向量候选按文档平均配额，而 BM25 是过滤集合上的全局排名；两路候选策略不一致。
2. `chat.py` rerank 截断后再为缺席文档补 top-1，追加结果不再 rerank，返回上下文可能超过 `top_k`。
3. RAG prompt 明确要求尽量使用更多不同文档，可能诱导无关引用，而不是以相关性/证据充分性为准。
4. BM25 索引属于请求内服务实例：每次 API 调用重新从数据库加载；`/rebuild-index` 只重建该次请求实例，返回后即丢失。
5. RRF 单测只验证共享结果排首位，没有覆盖文档过滤、阈值、去重、分数稳定性和跨文档公平性。

## 6. LangGraph、异步任务与 MCP

LangGraph 的状态累加器会累加每轮查询和结果，最终去重仅在累计结果超过 10 时发生；不超过 10 时可能把重复 chunk 送入生成。第二轮 query refinement 与最终回答均调用 DeepSeek，无降级/重试/结构化错误处理。

Celery 任务真实创建独立 async engine/session 处理文档，但未配置 retry、ack、超时、死信或幂等锁。解析成功而 embedding 失败时 chunks 已提交；重试会复用已有 chunks，这是有限幂等，但状态恢复和失败诊断不足。

MCP 的三个工具是真实实现，但 `ask_question` 在 DB session 内执行 rerank、随后 session 外生成；当前对象为普通 dict，不依赖 lazy ORM，因此结构上可行。本次未验证 MCP transport。

## 7. 前端

单页应用包含左侧会话、中部 chat/search/research 工作区、右侧知识库/文档/引用面板，API 全部指向真实 REST 路由。`FileUpload.tsx` 是另一个上传组件，但主页面自行实现上传；它可能是未使用遗留组件，不属于 Mock。

动态证据：2026-08-13 执行 `npm run build` 成功完成编译、类型检查、静态页面生成和 trace；出现 webpack cache snapshot warning，但未导致失败。未执行浏览器交互、可访问性、响应式或真实 API 联调。

## 8. 评测与测试可信度

`eval/eval_report.json` 显示 5/5 passed，但不能视为当前可复现基线：

- 报告时间为 2026-07-15；当前 `run_eval.py` 输出应含 `mode`、`retrieval`、`citation_precision` 和顶层 `ablations`，现有报告均没有，说明报告由旧版脚本生成。
- 数据集 `eval/test_dataset.json` 期待数据库中标题为 `test_doc.md` 的文档，但仓库没有建立该 fixture 的脚本。
- LLM 既生成答案又担任 judge，依赖外部 API、模型漂移和数据库现场状态；没有随机性/版本/输入数据快照。
- 5 个问题均是项目自描述，不能代表跨文档检索或引用正确性。

## 9. 实际运行记录

| 命令/检查 | 结果 | 说明 |
| --- | --- | --- |
| `npm run build`（frontend） | 成功 | Next.js 14.2.35 编译、lint/type check、4 个静态页生成成功；有非致命 webpack cache warning。 |
| `docker compose config --quiet` | 成功 | 使用临时空 `DOCKER_CONFIG` 后 Compose YAML 可解析。 |
| `python -m pytest backend/tests eval/test_metrics.py -q` | 失败 | PATH 中没有 `python`。 |
| bundled Python `-m pytest ...` | 失败 | Python 3.12.13 可执行，但没有安装 pytest；按 Phase 0 限制未安装依赖。 |
| `docker compose ps` / `docker info` | 失败 | Docker daemon pipe 不存在，Docker Desktop 未运行。 |
| 启动 Docker Desktop | 失败 | 当前会话请求启动被 OS `Access denied (os error 5)` 拒绝。 |
| `start.ps1` 完整启动 | 无法验证 | 该脚本首先要求 Docker engine；同一环境前置条件失败，未继续执行以避免无意义等待/构建。 |
| `eval/run_eval.py` | 未执行 | 依赖 PostgreSQL、embedding 模型、DeepSeek API 与预置 fixture；当前基础设施不可用，且可能产生外部费用。 |

当前项目能否完整启动：**本次不能确认；状态 5（环境无法验证）**。Compose 语法正确且服务定义完整，但没有任何本次容器健康、API、worker、MCP、前端联调证据。

## 10. Mock、硬编码与工作区卫生

- 未发现业务路径中的 Mock/fake 回答或假检索；外部模型调用是真实 HTTP 请求。
- 发现占位 API key：`backend/.env.example`，属于正常模板。
- 发现硬编码开发数据库凭据、端口、CORS origin、embedding 维度与模型 API 行为。
- 发现已有上传文件和 `.next` 构建产物；`.gitignore` 声明忽略它们，但 Git 仓库损坏/不完整，无法审计“未提交”状态。
- 实际 `.env` 含非空密钥变量的可能性存在；值未披露，且无法凭损坏的 Git 元数据判断是否曾提交。

## 11. Phase 0 验收结论

Phase 0 的事实盘点已完成。后续最优先不是扩展 UI，而是先修复跨文档检索语义并建立可复现 fixture/评测门禁；随后再引入正式迁移和后端可靠性。详细顺序见 `docs/UPGRADE_PLAN.md`。本阶段到此停止。
