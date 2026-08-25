# InsightFlow 最终验收

验收日期：2026-08-13（Asia/Shanghai）  
范围：Phase 0—5 当前工作区；结论仅基于实际代码、命令、服务和浏览器证据。

## 1. 最终结论

InsightFlow 已形成可构建的 Compose 全栈交付配置和可执行验收脚本。数据库迁移、后端回归、检索契约、SSE 状态机、前端 lint/typecheck/test/build、宿主 FastAPI/Next.js 联调、会话刷新恢复和评测空状态均已实际通过。

本机 Docker Desktop 无法被当前用户访问：CLI 启动请求返回成功，但随后报告无法写入 `C:\Users\28891\AppData\Local\Docker\log\host`，且访问 `docker_engine` named pipe 为 permission denied。真实 Embedding smoke test又因当前网络无法从 HuggingFace 或备用源下载模型而失败。虽然本地 `.env` 检测到非占位 DeepSeek Key，本轮没有在 Embedding 失败后继续产生收费模型请求。因此 PostgreSQL/pgvector、Redis、独立 Celery worker、真实 Embedding、DeepSeek、完整容器 E2E 和正式项目评测没有被标记为通过。

没有部署账号、域名或平台权限，本次只交付可部署配置和文档，没有虚构在线地址。

## 2. 工程化交付

- Compose 默认服务：`postgres`、`redis`、`migrate`、`backend`、`worker`、`frontend`；MCP 为可选 `mcp` profile。
- `migrate` 必须成功退出后 backend/worker 才启动；backend 又健康后 frontend 才启动。
- PostgreSQL、Redis、backend、worker、frontend 和可选 MCP 均有与自身能力匹配的健康检查。
- PostgreSQL、Redis、上传、评测和模型缓存均使用命名卷；Redis 开启 AOF。
- 后端和前端生产镜像使用非 root 用户；生产 Compose 不再把宿主源码覆盖进镜像。
- 数据库密码和 DeepSeek Key 通过根目录 `.env` 注入；Compose 对空值 fail-fast；示例文件不含真实值。
- Embedding cache 改为可配置，容器固定 `/models/fastembed`，消除了 Windows 上硬编码 `/root` 的权限问题。
- CORS 来源改为环境配置。
- `start.ps1` 支持构建、`--wait`、迁移确认、readiness、停止和可选 MCP。
- 提供公开 demo 文档、真实上传等待脚本、Compose 基础验收脚本和无 Mock 的 API E2E 脚本。

## 3. 验收矩阵

| 用户要求 | 状态 | 实际证据 |
| --- | --- | --- |
| Docker Compose 构建与启动 | 阻塞 | `docker desktop start` 后仍因日志目录和 pipe 权限失败；未产生镜像/容器通过证据。 |
| 必要服务健康 | 部分验证 | Compose 六个默认服务和探针渲染成功；宿主 FastAPI `/api/health` 200，`/api/ready` 在 Redis 缺失时于 1.5s 内真实返回 503 和 `database=ok, redis=TimeoutError`。容器健康未验证。 |
| 数据库迁移 | 已验证（SQLite）/ PostgreSQL 阻塞 | 干净库 upgrade 到 `0002_product_persistence (head)`，`alembic check` 为 `No new upgrade operations detected.`；真实 PostgreSQL 未启动。 |
| 上传文档 | 部分验证 | Phase 4 浏览器真实上传已验证 broker 不可用失败状态；成功异步处理因 Docker/Redis/模型阻塞。 |
| 异步解析/切分/Embedding/入库 | 阻塞 | Celery 状态机与幂等回归通过；独立 worker 未启动；真实模型三轮下载失败，最终 `ValueError: Could not load model BAAI/bge-small-en-v1.5 from any source.` |
| 创建会话并提问 | 部分验证 | 真实会话 API、SSE 失败/取消及刷新恢复通过；模型成功回答未验证。 |
| 检索与 LangGraph | 部分验证 | 9 项 Phase 1 契约及完整回归通过；宿主 SSE 已真实执行 plan/search 并报告环境失败；真实 pgvector/模型路径阻塞。 |
| SSE 流式回答 | 部分验证 | accepted、节点、failed、取消和数据库 `cancelled` 已验证；DeepSeek complete 回答未验证。 |
| 点击引用 | 自动化契约通过 / 浏览器成功链路阻塞 | 引用编号→Chunk 映射测试通过；无真实成功回答，未伪造浏览器引用。 |
| 刷新恢复会话 | 已验证 | Phase 5 浏览器创建会话、reload 后历史会话仍可见；控制台无应用错误。 |
| 失败任务与有限重试 | 已验证状态机 / 独立 worker 阻塞 | 任务错误、retrying、最大重试和幂等测试通过；Phase 4 页面展示真实 Redis 错误与重试入口。 |
| 三方案真实评测 | 阻塞（可信度门禁） | 正式命令返回 exit 3，拒绝 `pending_review`；没有人工 Gold，不生成项目 Recall/MRR/nDCG。 |
| 评测页面读取真实结果 | 已验证空状态 | Phase 5 浏览器确认“暂无已验证结果”；没有示例数字，控制台无应用错误。 |
| 后端测试与前端构建 | 已验证 | 后端 `31 passed in 3.34s`；前端 lint 零警告、typecheck 通过、3 tests passed、Next build 成功。 |

## 4. 实际执行记录

### Compose 与 Docker

使用仅用于配置展开的临时环境值执行：

```powershell
$env:POSTGRES_PASSWORD='phase5-validation-only'
$env:DEEPSEEK_API_KEY='validation-placeholder-not-a-real-key'
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example config --services
```

结果：配置有效；服务为 postgres、migrate、redis、backend、frontend、worker；`mcp` profile 可识别。Docker 命令同时打印用户 Docker config 读取拒绝 warning。

尝试启动：

```powershell
docker desktop start
docker desktop status
docker info
```

第一条显示 `Starting Docker Desktop`；随后 status/info 失败，原因为 Docker 日志目录 Access denied 和 `docker_engine` pipe permission denied。未执行 `compose up`，因为引擎不可用。

### 迁移与后端

```powershell
$env:DATABASE_URL='sqlite+aiosqlite:///./phase5_migration.db'
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

结果：`0002_product_persistence (head)`；没有 schema drift。

```powershell
$env:DATABASE_URL='sqlite+aiosqlite:///./phase5_test.db'
python -m pytest tests ../eval/test_metrics.py ../eval/test_dataset.py -q
```

结果：`31 passed in 3.34s`。测试结束仍有既存 SQLite/多事件循环 SQLAlchemy 连接回收 warning。首次对未迁移空库直接运行测试出现 13 个 `no such table` 失败；按正式迁移→测试顺序执行后全部通过，说明启动迁移依赖不可省略。

### 前端

```powershell
npm run lint
npm run typecheck
npm test
npm run build
```

结果：ESLint 无 warning/error；类型检查通过；3/3 测试通过；Next.js 14.2.35 production build 成功。webpack cache snapshot warning 不影响退出码。

### Embedding 与评测

真实 FastEmbed smoke test使用可写的 `./phase5_model_cache`，连续尝试 HuggingFace 和备用源，三轮均失败，最终退出 1。没有生成或估算向量。

```powershell
python eval/run_eval.py --fixture --dataset eval/datasets/synthetic_fixture_v1.json --output-dir eval/reports/phase5_fixture --top-k 5 --seed 20260813
# exit 0，仅公式/产物管线

python eval/run_eval.py --dataset eval/datasets/candidate_project_v1.json --output-dir eval/reports/phase5_formal_blocked --top-k 5 --seed 20260813
# exit 3：Formal evaluation refused: dataset is not human-approved.
```

Fixture 数字不是项目效果，未写入简历。正式项目指标不存在。

### 宿主服务与浏览器

干净 SQLite 迁移后启动 FastAPI 8025/8027 和 Next.js 3025。`/api/health` 返回 200；缺少 Redis 时 `/api/ready` 在超时上限内返回 503 并逐项标记。浏览器确认：

- 页面读取空文档/会话/评测真实 API；
- “暂无已验证结果”可见；
- 创建会话后 reload，历史会话仍可见；
- 页面控制台 warning/error 过滤结果为空。

Phase 4 已另行验证真实上传失败、节点失败和可靠取消；本轮不重复用 Fixture 冒充成功引用。

## 5. 修改文件

- `docker-compose.yml`
- `backend/Dockerfile`
- `frontend/Dockerfile`
- `.dockerignore`、`frontend/.dockerignore`
- `.env.example`、`backend/.env.example`、`.gitignore`
- `backend/app/config.py`
- `backend/app/main.py`
- `backend/app/tasks.py`
- `backend/app/routers/evaluations.py`
- `backend/app/services/embedding_service.py`
- `backend/app/services/rag_service.py`
- `frontend/package.json`、`frontend/package-lock.json`、`frontend/.eslintrc.json`
- `start.ps1`
- `demo/architecture.md`、`demo/product.md`
- `scripts/demo_import.py`、`scripts/api_e2e.py`、`scripts/compose_acceptance.ps1`
- `README.md`
- `docs/FINAL_ACCEPTANCE.md`
- `docs/UPGRADE_PLAN.md`

## 6. 需要执行的后续步骤

在具有 Docker Desktop 权限和可访问模型源的机器上：

1. 修复当前用户对 Docker Desktop 日志目录和 Docker engine 的权限，确认 `docker info` 有 Server 输出。
2. `Copy-Item .env.example .env`，填写随机 `POSTGRES_PASSWORD` 与有效 `DEEPSEEK_API_KEY`。
3. 执行 `.\start.ps1`；确认 `docker compose ps` 中 postgres、redis、backend、worker、frontend 均 healthy，migrate 为 exited 0。
4. 执行 `powershell -File scripts/compose_acceptance.ps1`。
5. 执行 `docker compose exec -T backend python /scripts/api_e2e.py --api http://127.0.0.1:8000 --demo-dir /demo`。
6. 在 Web 中打开已生成引用、刷新会话并检查网络/控制台；E2E 脚本已验证 API 层，浏览器补验点击定位。
7. 人工审核 `eval/datasets/candidate_project_v1.json`，冻结真实 corpus manifest 后再运行正式评测；未经该步骤不要发布指标。

## 7. 简历项目描述（仅含已验证事实）

**InsightFlow｜可追溯证据的 AI 知识库与检索工作台**  
技术栈：FastAPI、SQLAlchemy/Alembic、PostgreSQL/pgvector、Redis/Celery、LangGraph、Next.js、Docker Compose

- 设计 BM25、向量召回与 RRF 融合的统一检索链路，修正显式多文档范围下的候选配额、稳定 Chunk ID 去重和软多样性策略，保留召回通道、原始排名、融合排名及证据不足诊断，并以 9 项确定性检索/引用契约测试验证。
- 构建文档、Chunk、会话、消息、异步任务、检索记录、引用和 LangGraph 节点事件的持久化模型与 Alembic 迁移；实现 SSE 节点状态、失败/取消持久化、Celery 有限重试和 Chunk 幂等写入。
- 将 Next.js 原型产品化为真实 API 驱动的文档、会话、检索、引用和评测工作台；前端 lint、类型检查、测试与生产构建通过，浏览器验证会话刷新恢复、评测可信空状态和无应用控制台错误。
- 建立带 Gold Label 审核门禁的三方案消融评测框架，统一输出逐题 JSONL、汇总 JSON、对比 CSV、Markdown 与失败分类；正式命令会拒绝未经人工审核的数据，避免合成 Fixture 被误报为项目指标。
- 完成非 root 多阶段镜像、迁移先行、分层健康检查、命名卷、环境密钥注入、Demo 导入和无 Mock API E2E 脚本；Compose 配置渲染、干净库迁移及 31 项后端回归测试通过。

本描述没有写入部署上线、容器运行成功或未经人工审核的评测数字。
