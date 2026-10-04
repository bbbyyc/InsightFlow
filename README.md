# InsightFlow

InsightFlow 是一个可追溯证据的 AI 知识库应用：文档经异步解析、结构化切分与 Embedding 入库后，由 BM25、pgvector 和 RRF 混合检索提供证据，LangGraph 以 Plan → Search → Rewrite → Generate 节点生成带 `[N]` 引用的回答。Next.js 页面展示真实任务、召回、引用、工作流和评测状态。

## 已实现能力

- PDF、Markdown、TXT 上传、删除、状态查询、失败原因和有限重试
- Celery + Redis 异步解析、切分、Embedding、入库及幂等 Chunk 写入
- PostgreSQL + pgvector 持久化文档、Chunk、会话、消息、任务、检索、引用、Agent 事件和评测运行
- BM25、pgvector、BM25 + vector + RRF 三种检索；显式多文档范围使用软覆盖，不强塞低相关证据
- BM25 跨请求复用索引，数据库版本号检测跨进程语料变更；并发请求合并构建，分词、建索引和评分在线程中执行
- 持久化会话、SSE 回答、节点状态/耗时、可靠取消和可点击引用
- 评测数据门禁、三方案同条件消融、逐题 JSONL、汇总 JSON、CSV、Markdown 和失败分类
- 无人工审核 Gold Label 时拒绝正式指标，并在页面显示“暂无已验证结果”

## 架构与服务

默认 Compose 启动 `postgres`、`redis`、一次性 `migrate`、`backend`、`worker` 和 `frontend`。`mcp` 是可选 profile，不重复引入数据库、队列或检索组件。

```text
Browser → Next.js → FastAPI → PostgreSQL/pgvector
                         ├→ Redis → Celery worker → parser/embedding
                         └→ DeepSeek API（生成、改写、重排，可选外部依赖）
```

## 一条命令启动

前置条件：Docker Engine / Docker Desktop、Docker Compose v2。首次下载基础镜像和 FastEmbed 模型需要外网。

```powershell
Copy-Item .env.example .env
# 编辑 .env：填写 POSTGRES_PASSWORD 和 DEEPSEEK_API_KEY。
.\start.ps1
```

或者直接使用 Compose：

```powershell
docker compose up -d --build --wait
```

服务地址：

- Web：<http://localhost:3000>
- FastAPI：<http://localhost:8000>
- Swagger：<http://localhost:8000/docs>
- Liveness：<http://localhost:8000/api/health>
- Backend readiness（DB、Redis）：<http://localhost:8000/api/ready>；worker 由 Compose 独立 healthcheck 验证

停止服务：

```powershell
.\start.ps1 -Stop
# 删除数据库、Redis、上传、模型和评测卷（会永久清除本地数据）：
docker compose down -v
```

启用 MCP SSE：

```powershell
.\start.ps1 -WithMcp
# 或 docker compose --profile mcp up -d --build --wait
```

## 配置与密钥

根目录 `.env.example` 是 Compose 唯一示例配置，不包含真实密钥。`.env` 被 Git 忽略。生产环境应由秘密管理服务注入 `POSTGRES_PASSWORD` 和 `DEEPSEEK_API_KEY`，不要把 `.env` 烘焙进镜像。

关键变量：

| 变量 | 说明 |
| --- | --- |
| `POSTGRES_PASSWORD` | 必须替换的数据库密码 |
| `DEEPSEEK_API_KEY` | 完整栈必需，用于生成、改写、重排和 Judge |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | 默认多语言 384 维模型；修改维度需要数据库迁移 |
| `TASK_MAX_RETRIES` | Worker 最大重试次数，默认 3 |
| `CORS_ORIGINS` | 逗号分隔的允许来源 |
| `*_PORT` | 宿主机映射端口 |

`backend/.env.example` 仅用于不通过 Compose 的本地后端开发。

## 数据库迁移

Compose 会先运行一次性 `migrate` 服务；迁移成功后 backend/worker 才启动。

当前迁移头为 `0004_bm25_revision`，新增 `corpus_revision` 表和文档/Chunk 变更触发器。升级现有环境时，必须先用新版镜像运行迁移，再启动新版后端；本地开发也必须执行 `alembic upgrade head`。已验证 SQLite 升级、降级再升级及 schema 一致性；后续 Docker 验收也通过 PostgreSQL 新迁移和缓存失效测试。但 PostgreSQL `alembic check` 仍发现 HNSW 索引和 JSON/JSONB 的模型声明差异，详见 运行验收（本地记录）。

```powershell
docker compose run --rm migrate
docker compose run --rm migrate python -m alembic current
docker compose run --rm migrate python -m alembic check
```

本地 Python 开发：

```powershell
cd backend
python -m alembic upgrade head
uvicorn app.main:app --reload
```

## Demo 数据导入与真实 API 验收

仓库 `demo/` 下两份 Markdown 是公开、确定性的演示语料，但不是 Gold Label 评测集。服务健康后：

```powershell
docker compose exec -T backend python /scripts/demo_import.py /demo/architecture.md /demo/product.md --api http://127.0.0.1:8000
```

完整真实 API 验收（要求 Embedding 下载成功且配置有效 DeepSeek Key）：

```powershell
docker compose exec -T backend python /scripts/api_e2e.py --api http://127.0.0.1:8000 --demo-dir /demo
powershell -ExecutionPolicy Bypass -File scripts/compose_acceptance.ps1
```

脚本会真实上传两文档、等待 Celery、验证 BM25/vector/hybrid、创建会话、消费 SSE、检查引用和会话持久化；任何外部服务失败都会使命令失败，不含 Mock。

## 评测

当前项目候选集 `eval/datasets/candidate_project_v1.json` 尚未人工审核，正式命令会拒绝它，不能生成项目效果数字。人工审核流程见 `eval/README.md`。

公式与报告管线自测（结果不是项目效果）：

```powershell
docker compose exec -T backend python /eval/run_eval.py --fixture --dataset /eval/datasets/synthetic_fixture_v1.json --output-dir /app/data/evaluations/synthetic
```

人工审核 Gold 和冻结语料后，才运行正式三方案评测：

```powershell
docker compose exec -T backend python /eval/run_eval.py --dataset /eval/datasets/project_approved.json --output-dir /app/data/evaluations/formal --top-k 5 --seed 20260813
```

需要页面读取正式结果时，通过 `POST /api/evaluations` 创建异步运行；输出和数据库记录均由 worker 写入。未经审核的运行不会显示为正式指标。

## 测试与构建

```powershell
# 后端（必须先迁移测试库）
cd backend
$env:DATABASE_URL='sqlite+aiosqlite:///./phase5_test.db'
python -m alembic upgrade head
$env:PYTHONPATH='.;../eval'
python -m pytest tests ../eval/test_metrics.py ../eval/test_dataset.py -q

# 前端
cd ../frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

## BM25 索引优化与性能复现

此前每次搜索都重新读取全库 Chunk、分词和构建 BM25 索引。现在同一数据库引擎、同一事件循环内复用索引快照，每次搜索读取数据库版本号，语料变更后才重建。版本号随写事务更新，覆盖 Celery、其他 API 进程和直接 SQL 写入；构建前后检查版本，避免发布与版本不一致的快照。

在仓库根目录、已安装后端依赖的 Python 环境中运行：

```powershell
python scripts/benchmark_bm25_cache.py --chunks 1000 --requests 50
```

脚本创建临时 SQLite 数据库，不修改应用数据库，也不调用 Embedding 或大模型。2026-09-18 本机结果：

| 模式 | P50 | P95 |
| --- | ---: | ---: |
| 每次强制重建索引 | 303.88 ms | 347.27 ms |
| 版本检查后复用索引 | 2.22 ms | 2.87 ms |

测试使用 1,000 个合成 Chunk，每组 50 次串行请求，分词器已预热；两组排名与分数一致。这是 SQLite 服务层对比，不包含 HTTP、向量检索或生成，不能替代完整 Hybrid 链路压测，也不能直接与旧报告的 10 并发 P95 比较。

每个 API 进程分别持有全库索引；语料变更仍需全量重建，尚未实现增量索引。详细设计、写入竞争与内存边界见 BM25 优化说明（本地记录），原始结果见 基准 JSON（本地记录）。

## 开发与生产

开发可启动基础设施后，在宿主机运行 FastAPI 和 `npm run dev`；设置 `API_PROXY_TARGET=http://127.0.0.1:8000` 后再启动/构建 Next.js。生产镜像以非 root 用户运行，不挂载源码，数据库/Redis/上传/模型/评测均使用命名卷，并具备 restart policy 和健康检查。

部署到单机或 VM 时复制 `docker-compose.yml`、镜像构建上下文和生产 `.env`，只对外开放 frontend；backend、PostgreSQL 和 Redis 应置于私有网络或由反向代理保护。TLS、域名、备份、集中日志和 secret manager 由目标平台配置。本仓库没有部署账号、域名或已上线地址。

## 常见故障

- `docker_engine` pipe 不存在 / permission denied：启动 Docker Desktop，并确认当前用户有访问 Docker 的权限。
- `migrate` 失败：查看 `docker compose logs migrate postgres`；确认密码一致和数据卷权限。
- `/api/ready` 返回 503：响应会分别给出 database、redis、worker 的失败类型；检查对应服务日志。
- Worker 一直下载模型：保留 `model_cache` 卷并检查代理/证书；模型缓存目录为 `/models/fastembed`。
- 回答 401/403：配置有效 `DEEPSEEK_API_KEY` 后重启 backend 和 worker。
- 文档显示 retrying/failed：查看数据库任务错误和 `docker compose logs worker`；默认有限重试 3 次。
- 页面请求错误：Compose 构建阶段已将代理固定为 `http://backend:8000`；本地生产构建必须在 `npm run build` 前设置 `API_PROXY_TARGET`。
- 正式评测被拒绝：这是 Gold Label/语料门禁；完成人工审核和 corpus manifest 冻结后再运行，不要改代码绕过。

## 当前验收状态

按验证日期区分，历史通过记录不代表本轮重新执行：

- **2026-09-18/19 Docker 补验：** 恢复 Docker 后构建当前源码镜像，五个常驻服务 healthy，PostgreSQL 迁移到 `0004_bm25_revision`。真实上传两份文档，经 Celery/Embedding 入库，三种检索均返回结果，Agent 流式回答返回 2 条引用且会话可重新读取。PostgreSQL BM25 并发复用、更新和删除失效实测通过。`alembic check` 检测到两项已有声明差异，未将其标为通过；未重跑完整 Hybrid 并发压测或浏览器点击定位。构建环境修复、E2E 幂等键修复与证据见 本轮验收（本地记录）。

- **2026-09-18：** BM25 优化后，后端与评测回归 **38 项通过**，包括并发索引复用、不同数据库连接写入后的失效、增删改、事务回滚和构建期间写入。SQLite 迁移升降级及 `alembic check` 通过；已完成上述服务层性能对比。测试结束仍有既有 SQLAlchemy 连接回收警告。本轮 Docker 引擎未就绪，尚未验证新增 PostgreSQL 触发器、容器端到端性能，也未重跑前端构建。
- **2026-08-28：** 后续生成语料基准（本地记录）及审计（本地记录）记录了本地 PostgreSQL/pgvector、Redis/Celery、真实 Embedding 的运行验证：20 篇文档、60 个 Chunk、100 条查询及小规模并发测试。这是程序化生成语料，不能作为真实业务效果或生产容量证明。
- **2026-08-13：** 历史验收（本地记录）记录了 Compose 配置、SQLite 迁移、当时的 31 项后端回归、前端 lint/typecheck/3 项测试/构建和宿主浏览器验证；其中 Docker 阻塞是当时的环境状态，不应覆盖后续验证记录。

目前仍缺少独立人工审核业务测试集、生产负载及多实例高可用验证。

## License

MIT
