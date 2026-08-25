# InsightFlow Phase 3 结果

完成日期：2026-08-13（Asia/Shanghai）  
范围：后端、数据库迁移、LangGraph 事件、持久化和异步任务；未修改前端、Docker 编排或部署功能。

## 1. 结论

Phase 3 已完成当前环境内可执行的后端产品化工作。应用不再在启动时调用 `create_all`，数据库结构由 Alembic 管理；文档、Chunk、会话、消息、任务、检索、引用、Agent 运行/节点事件和评测运行均有数据库模型。上传和批量评测通过 Celery 投递，任务状态由数据库读取，具备有限重试、错误持久化和幂等键；重复上传或重复投递不会重复创建文档任务或 Chunk。

实际验证结果：

- SQLite 验证库：`upgrade head → downgrade 0001 → upgrade head → alembic check` 全部成功；最终为 `0002_product_persistence (head)`，无待生成 schema 操作。
- 后端完整测试：`30 passed in 3.39s`。
- Uvicorn 实际启动成功，`/api/health`、`/api/ready`、`/openapi.json` 均返回 HTTP 200。
- Docker 客户端存在，但 daemon 不可用，且读取用户 Docker 配置被操作系统拒绝。因此未运行真实 PostgreSQL/pgvector、Redis broker、独立 Celery worker 或容器端到端测试；这些项目没有被标记为通过。

测试结束时 SQLAlchemy 仍打印一次 SQLite/多事件循环场景的连接垃圾回收警告，全部 30 个断言已通过。生产默认 PostgreSQL 引擎不使用该 SQLite 测试路径；此警告列为测试基础设施待清理项，不影响本次已验证的数据断言。

## 2. 数据模型与迁移

迁移：

- `backend/alembic/versions/0001_baseline.py`：从空库创建原有 documents、chunks、conversations、messages、conversation_documents；PostgreSQL 下创建 `vector` extension 和 Chunk embedding HNSW 索引。
- `backend/alembic/versions/0002_product_persistence.py`：增加内容 hash、文档处理错误/完成时间、会话更新时间；增加任务、检索记录、独立引用、Agent 运行、节点事件和评测运行表；增加 Chunk 与会话文档幂等唯一约束。
- `backend/alembic.ini`、`backend/alembic/env.py`、`backend/alembic/script.py.mako`：提供真实 Alembic 运行环境。

核心表：

| 表 | 持久化内容 | 关键约束 |
| --- | --- | --- |
| `documents` | 文件路径、SHA-256、解析状态、错误、Chunk 数、完成时间 | UUID 主键 |
| `chunks` | 内容、稳定 UUID、文档、位置、section、embedding/model、内容 hash | `(document_id, chunk_index)` 唯一 |
| `conversations` / `messages` | 历史会话、用户/助手消息、JSON 引用快照 | 消息级联删除 |
| `conversation_documents` | 会话选择的文档 | `(conversation_id, document_id)` 唯一 |
| `task_records` | 任务类型、数据库状态、幂等键、Celery ID、尝试次数、错误、结果摘要 | 幂等键和 Celery ID 唯一 |
| `retrieval_records` | 原始/改写查询、文档过滤、Top-K、Chunk ID、诊断、耗时、错误 | 数据库持久化状态 |
| `citations` | 消息到 document/chunk/retrieval 的引用映射 | `(message_id, citation_number)` 唯一 |
| `agent_runs` / `agent_node_events` | Plan/Search/Rewrite/Generate 状态、顺序、耗时、输入/结果摘要、失败 | `(agent_run_id, sequence)` 唯一 |
| `evaluation_runs` | 数据集 hash、参数、输出目录、摘要、错误和关联任务 | 关联 `task_records` |

节点事件只保存节点名、运行状态、耗时和有限输入/结果摘要，不保存模型内部思维链、隐藏推理或完整提示词。

## 3. API 与行为

### 文档与任务

- `POST /api/documents/upload`：支持 PDF/Markdown/TXT，校验空文件与大小；接受 `Idempotency-Key`。响应包含文档和数据库任务记录；同一键重交返回原文档/任务并标记 `created=false`。
- `GET /api/documents`、`GET /api/documents/{id}`、`DELETE /api/documents/{id}`：查询、详情/Chunk、删除。删除会清理数据库记录及实际上传文件。
- `GET /api/tasks/{task_id}`：只读数据库中的真实状态、尝试次数、错误和结果摘要，不使用内存字典或 Mock 状态。
- broker 投递失败时 API 返回 503，并将任务与文档状态写为 `failed`，不会留在伪 `pending`。

### 会话与回答

- `POST /api/chat/conversations`：创建会话并可关联文档。
- `GET /api/chat/conversations`、`GET/PATCH/DELETE /api/chat/conversations/{id}`：历史会话、详情、重命名和删除。
- `POST/DELETE /api/chat/conversations/{id}/documents...`：维护会话文档范围。
- `POST /api/chat`：可指定或续接会话；保存用户/助手消息、检索记录和逐条引用。
- `POST /api/agent`：同步 Agent 回答也创建/续接会话，保存消息、运行和节点事件。
- `POST /api/agent/stream`：SSE 返回 `accepted`（含 run/conversation ID）、`plan`、`search`、`rewrite`、`generate`、`complete` 或 `failed`；客户端中断时取消后台执行并记录 `cancelled`。

### 异步评测与健康检查

- `POST /api/evaluations`：基于数据集 SHA-256 和关键参数创建幂等评测任务；评测由 Celery 执行现有 Phase 2 统一命令。
- `GET /api/evaluations/{run_id}`：读取数据库评测运行状态、参数、输出和错误。
- `GET /api/health`：不依赖外部模型的 liveness。
- `GET /api/ready`：执行数据库 `SELECT 1`；失败返回 503。

## 4. 异步任务、重试与幂等

`backend/app/tasks.py` 复用现有 Celery/Redis 架构：JSON 序列化、late ack、worker lost 拒绝、prefetch=1、软/硬时限、最大三次重试和指数退避。每次尝试先更新数据库记录；成功、等待重试、终态失败都保存状态和错误。

文档任务幂等分两层：

1. 上传 API 的 `Idempotency-Key`（未提供时用内容 SHA-256 + 规范化文件名）唯一；
2. Chunk 用 `(document_id, chunk_index)` upsert/唯一约束，已存在 Chunk 不再重复插入，缺失或模型变化的 embedding 才重新生成。

Celery 重复投递已成功的任务时直接返回数据库中的 `result_summary`，不会再次处理文档。批量评测用数据集 hash、Top-K、随机种子与生成/Judge 开关组成默认幂等键。

## 5. 修改文件

新增：

- `backend/alembic.ini`
- `backend/alembic/env.py`
- `backend/alembic/script.py.mako`
- `backend/alembic/versions/0001_baseline.py`
- `backend/alembic/versions/0002_product_persistence.py`
- `backend/app/models/task_record.py`
- `backend/app/models/retrieval_record.py`
- `backend/app/models/citation.py`
- `backend/app/models/agent_run.py`
- `backend/app/models/evaluation_run.py`
- `backend/app/routers/tasks.py`
- `backend/app/routers/evaluations.py`
- `backend/app/services/task_service.py`
- `backend/app/services/persistence_service.py`
- `backend/app/services/ids.py`
- `backend/tests/test_phase3_persistence.py`
- `backend/tests/test_phase3_sse.py`
- `backend/tests/test_phase3_celery.py`
- `backend/requirements-test.txt`
- `docs/PHASE3_RESULT.md`

修改：

- `backend/app/config.py`、`backend/.env.example`
- `backend/app/database.py`、`backend/app/main.py`、`backend/app/tasks.py`
- `backend/app/models/__init__.py`、`document.py`、`chunk.py`、`conversation.py`、`conversation_document.py`、`message.py`
- `backend/app/services/document_service.py`、`agent_service.py`
- `backend/app/routers/documents.py`、`chat.py`、`agent.py`
- `docs/UPGRADE_PLAN.md`

没有修改前端文件、Docker Compose 或部署脚本。

## 6. 实际命令与结果

测试环境使用项目内隔离 Python 3.12 虚拟环境 `.venv-phase3`，生产依赖保持不变；SQLite 测试驱动记录在 `backend/requirements-test.txt`。

迁移与漂移检查：

```powershell
$env:DATABASE_URL='sqlite+aiosqlite:///./phase3_migration_verify.db'
$env:PYTHONPATH='.'
..\.venv-phase3\Scripts\python.exe -m alembic upgrade head
..\.venv-phase3\Scripts\python.exe -m alembic downgrade 0001
..\.venv-phase3\Scripts\python.exe -m alembic upgrade head
..\.venv-phase3\Scripts\python.exe -m alembic current
..\.venv-phase3\Scripts\python.exe -m alembic check
```

结果：成功；`0002_product_persistence (head)`；`No new upgrade operations detected.`

完整回归：

```powershell
$env:DATABASE_URL='sqlite+aiosqlite:///./phase3_test.db'
$env:UPLOAD_DIR='./phase3_test_uploads'
$env:PYTHONPATH='.;../eval'
..\.venv-phase3\Scripts\python.exe -m pytest tests ../eval/test_metrics.py ../eval/test_dataset.py -q
```

结果：`30 passed in 3.39s`。覆盖原有检索回归、Phase 2 数据/指标、数据库/API、创建并续接会话、会话历史、SSE 成功/失败/取消、任务成功/重试/终态失败/重投幂等、broker 投递失败，以及上传→解析切分→embedding→检索→回答→引用落库集成。外部 embedding、向量 SQL 和 LLM 在该集成测试中用确定性替身隔离；数据库、API、解析、切分、持久化和状态机是真实执行。

启动验证：

```powershell
..\.venv-phase3\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8013
Invoke-RestMethod http://127.0.0.1:8013/api/health
Invoke-RestMethod http://127.0.0.1:8013/api/ready
Invoke-RestMethod http://127.0.0.1:8013/openapi.json
```

结果：Uvicorn 启动完成，三个请求均为 HTTP 200；验证后已正常停止进程。

Docker/真实服务验证：

```powershell
docker version
docker compose config --quiet
```

结果：失败。Docker CLI 版本 29.6.1 可用，但 `docker_engine` named pipe 不存在；同时 `C:\Users\28891\.docker\config.json` 读取被拒绝。此前尝试启动 Docker Desktop也被操作系统拒绝。因此无法启动 PostgreSQL、Redis 和 worker。

## 7. 适用条件、边界与未验证项

- 生产路径仍以 PostgreSQL 16 + pgvector、Redis 7、Celery 为目标；SQLite 仅用于当前受限环境下的真实 ORM/API/migration 测试，不替代 pgvector 验证。
- PostgreSQL extension/HNSW DDL 已写入迁移但未对真实 PostgreSQL 执行；Redis broker 投递、独立 worker 消费、worker 崩溃后的 redelivery 和容器重启恢复未验证。
- 没有可用 DeepSeek API Key，也未下载/调用真实 embedding 模型；因此真实模型生成、API token usage、真实向量召回与模型故障链路未验证。测试不生成虚构模型结果。
- Phase 2 仍缺少人工审核 Gold Label；评测任务可持久化和异步执行，但不因此产生正式项目指标。
- readiness 当前只证明数据库可用；Redis、worker 和模型就绪度需要在 Phase 5 容器环境中增加分层探针。
- 上传默认幂等键会把“相同文件名 + 相同内容”视为同一提交；需要强制创建独立副本时，调用方必须提供新的 `Idempotency-Key`。
- 当前 SQLite 测试结束存在一次连接回收警告；应在 Phase 5 的长生命周期真实服务测试中确认连接池与流式断连清理无泄漏。
- Docker Compose 中的开发默认数据库密码属于已有硬编码开发配置，本阶段未改部署文件；生产 secret 外置留在 Phase 5。

Phase 3 到此停止，未开始 Phase 4。
