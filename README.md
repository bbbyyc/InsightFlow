# InsightFlow

InsightFlow 是一个可追溯证据的 AI 知识库应用：文档经异步解析、结构化切分与 Embedding 入库后，由 BM25、pgvector 和 RRF 混合检索提供证据，LangGraph 以 Plan → Search → Rewrite → Generate 节点生成带 `[N]` 引用的回答。Next.js 页面展示真实任务、召回、引用、工作流和评测状态。

## 已实现能力

- PDF、Markdown、TXT 上传、删除、状态查询、失败原因和有限重试
- Celery + Redis 异步解析、切分、Embedding、入库及幂等 Chunk 写入
- PostgreSQL + pgvector 持久化文档、Chunk、会话、消息、任务、检索、引用、Agent 事件和评测运行
- BM25、pgvector、BM25 + vector + RRF 三种检索；显式多文档范围使用软覆盖，不强塞低相关证据
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
cd frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

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

本工作区已通过 Compose 配置渲染、SQLite 迁移与 31 项后端回归、前端 lint/typecheck/3 项测试/生产构建，以及 Phase 4 的真实宿主浏览器状态验证。当前 Codex 会话无法启动 Docker Desktop（操作系统拒绝访问），所以本次没有把 PostgreSQL/pgvector、Redis、独立 worker、容器 Embedding/DeepSeek 和容器端到端链路标记为已验证。完整证据与后续命令见 `docs/FINAL_ACCEPTANCE.md`。

## License

MIT
