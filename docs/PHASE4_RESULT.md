# InsightFlow Phase 4 结果

完成日期：2026-08-13（Asia/Shanghai）  
范围：前端产品化，以及为前端提供真实进度、评测列表、检索证据和可靠 SSE 中断语义所必需的后端契约补充；未开发部署功能，未使用 Mock 数据或虚构评测指标。

## 1. 结论

Phase 4 的前端实现已经完成。现有 Next.js 单页原型被整理为一个连接真实 FastAPI 的工作台，包含文档库、对话工作区、检索实验室和评测结果四类能力。所有任务状态、失败原因、会话、SSE 事件、检索 Chunk、引用和评测结果均从后端读取；前端没有内置示例指标、硬编码引用或伪任务状态。

已实际通过：

- TypeScript 类型检查；
- 3 项前端契约测试；
- Next.js 生产构建；
- 31 项后端、API、SSE、任务与评测回归测试；
- FastAPI 与 Next.js 同时启动后的浏览器交互验证；
- 会话创建与刷新恢复；
- SSE 节点事件、真实失败展示以及显式中断后的数据库 `cancelled` 持久化；
- 无已审核项目评测结果时显示“暂无已验证结果”；
- Redis 不可用时，真实上传任务失败原因和“重新执行”入口正确展示。

当前机器不能运行 Docker daemon，Redis/Celery worker 和 PostgreSQL/pgvector 未能启动；Windows 本机 Embedding 初始化还会因默认缓存目录 `\\root` 报 `PermissionError`。因此“上传成功处理 → 真实检索 → LLM 成功回答 → 点击真实引用”的完整成功链路没有被宣称通过，留作 Phase 5 在完整运行栈中补验。

## 2. 前端能力

### 文档与任务

- 上传仅接受 PDF、Markdown 和 TXT；上传请求携带真实幂等键。
- 文档列表展示数据库文档状态、Chunk 数、最新任务状态、尝试次数、失败原因及真实进度。
- 处理进度对应解析、切分、Embedding、入库四个后端阶段，不使用定时伪进度。
- 失败文档可调用 `POST /api/documents/{id}/retry` 重新投递；删除调用真实删除 API。
- 轮询仅在文档或任务仍为 pending/running/retrying 时启用。

### 会话与 SSE

- 支持创建、列出、恢复、删除会话，以及把选中文档范围写入会话。
- 用户消息和助手消息来自持久化会话 API；刷新后可重新打开并继续对话。
- SSE 客户端按帧解析 `accepted`、`plan`、`search`、`rewrite`、`generate`、`complete`、`failed`。
- 工作流面板仅显示节点、状态、耗时、结果数量等有限摘要，不展示内部思维链。
- SSE 响应头同时返回 `X-Agent-Run-Id` 和 `X-Conversation-Id`，使前端在首个 body frame 到达前即可可靠调用取消 API。浏览器实测取消后，UI 显示“本轮回答已由用户中断”，数据库最新 Agent Run 为 `cancelled`。

### 检索、引用与证据

- 检索实验室调用真实 `/api/search`，支持 BM25、vector、hybrid 及真实文档过滤。
- 每条结果显示 document、section、Chunk ID、内容、页码/原文位置、召回通道、通道内原始排名、RRF 排名及融合后排名。
- Agent 完成事件返回本轮真实 `retrieval_results`，可在回答下方打开证据详情。
- 回答中的 `[N]` 按后端 citation number 映射为按钮，点击后展示对应文档、章节、Chunk 内容及原文位置；没有引用对象时不会伪造映射。

### 评测结果

- 新增真实评测运行列表和详情 API 消费。
- 只有运行完成且报告元数据明确标记为 `project_evaluation` 时，页面才展示 BM25、vector、hybrid 的真实指标、逐题结果、平均/P50/P95 延迟、真实 Token usage 和失败分类。
- `pending_review`、synthetic fixture、失败或不完整运行不会冒充正式项目评测；没有正式结果时固定显示“暂无已验证结果”，不显示示例数字。

## 3. 必要的后端契约补充

- 文档列表和详情附带最新数据库任务，并返回处理错误、完成时间、页码及进度阶段。
- 文档处理服务在解析、切分、Embedding、入库节点写入真实进度；增加失败文档重试 API。
- 增加评测运行列表，并从运行输出目录读取真实 `cases.jsonl` 和 `failures.json`，不生成展示数据。
- Agent 完成结果增加真实召回结果；SSE 增加运行/会话响应头和显式运行取消 API。
- 取消状态写入增加并发保护，避免后续生成事件把 `cancelled` 覆盖成 running/failed/completed。
- Celery broker 投递使用 `retry=False`，使 broker 不可用时 API 能快速返回并持久化真实失败，而不是长时间挂起。

## 4. 修改文件

前端：

- `frontend/app/page.tsx`
- `frontend/app/globals.css`
- `frontend/app/layout.tsx`
- `frontend/lib/api.ts`
- `frontend/package.json`
- `frontend/tests/contracts.test.mjs`（新增）
- `frontend/components/FileUpload.tsx`（删除未使用旧组件）

后端契约及回归测试：

- `backend/app/services/task_service.py`
- `backend/app/services/document_service.py`
- `backend/app/services/agent_service.py`
- `backend/app/tasks.py`
- `backend/app/routers/documents.py`
- `backend/app/routers/evaluations.py`
- `backend/app/routers/agent.py`
- `backend/app/routers/search.py`
- `backend/tests/test_phase3_sse.py`
- `backend/tests/test_phase3_persistence.py`

文档：

- `docs/PHASE4_RESULT.md`
- `docs/UPGRADE_PLAN.md`

## 5. 实际命令与结果

前端：

```powershell
cd frontend
npm run typecheck
# 通过，无 TypeScript 错误

npm test
# 3 tests, 3 passed, 0 failed

npm run build
# Next.js 14.2.35：Compiled successfully
# / 为静态路由；页面 42.4 kB，First Load JS 130 kB
```

构建期间 webpack 文件快照缓存打印 warning，但编译、页面生成和退出码均成功。

`npm run lint` 已实际执行，但当前项目没有 ESLint 配置，也没有安装 `eslint` / `eslint-config-next`，Next.js 因而进入首次配置交互提示而没有产生 lint 结果。本阶段没有为了消除此环境门禁临时修改依赖；该项明确记为未通过，并列入 Phase 5 交付门禁。

回归：

```powershell
cd backend
$env:DATABASE_URL='sqlite+aiosqlite:///./phase4_empty.db'
$env:UPLOAD_DIR='./phase4_empty_uploads'
$env:PYTHONPATH='.;../eval'
..\.venv-phase3\Scripts\python.exe -m pytest tests ../eval/test_metrics.py ../eval/test_dataset.py -q
# 31 passed in 4.56s
```

测试退出后仍有一条既存的 SQLAlchemy/SQLite 多事件循环连接回收 warning；31 项断言全部通过，生产 PostgreSQL 路径不使用该 SQLite 测试连接池。

启动与浏览器联调：

```powershell
cd backend
$env:DATABASE_URL='sqlite+aiosqlite:///./phase4_empty.db'
..\.venv-phase3\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8020

cd frontend
$env:API_PROXY_TARGET='http://127.0.0.1:8020'
npm run dev -- --hostname 127.0.0.1 --port 3020
```

浏览器实际观察：

- `/api/documents`、`/api/chat/conversations`、`/api/evaluations`、SSE 请求均通过真实 Next.js 代理访问 FastAPI；应用自身控制台过滤结果为空。
- 创建“新会话”后刷新，历史列表仍可恢复该会话。
- 无正式评测运行的空库页面显示“暂无已验证结果”，未出现任何指标数字。
- 实际上传 Markdown 后，由于 localhost Redis 未启动，后端持久化失败并返回真实 `OperationalError: Error 10061 connecting to localhost:6379`；页面展示失败、错误详情及“重新执行”。
- 实际提问收到 plan/search 节点事件；Embedding 初始化因 `\\root` 缓存目录权限失败时，页面展示真实 `PermissionError`，没有伪造回答。
- 点击中断后 UI 显示用户中断；直接查询同一 SQLite 数据库，最新 Agent Run 状态为 `cancelled`。
- 浏览器插件自身出现过 Statsig 网络超时提示；它不来自 InsightFlow 页面。InsightFlow 应用控制台没有额外错误。

生产 `next start` 的 rewrites 目标在 build 时固化；如果构建时没有设置 `API_PROXY_TARGET`，之后只在启动时修改该变量不会改变已构建代理目标。当前 Docker 默认目标 `backend:8000` 与 Compose 一致；本机生产联调必须在 build 前设置该变量。该行为将在 Phase 5 的镜像构建和启动文档中固定。

## 6. 适用条件与已知边界

- 页面依赖 Phase 3 API schema；前端不维护可替代数据库/任务系统的内存状态。
- 正式评测展示依赖 Phase 2 生成且人工审核通过的 `project_evaluation` 报告。当前候选集仍为 `pending_review`，所以没有项目效果数字。
- 当前机器缺少可用 Docker daemon、Redis/Celery worker、PostgreSQL/pgvector 成功链路，无法完成真实异步处理成功验收。
- 本机 FastEmbed 默认缓存路径指向 `\\root`，在 Windows 上无权限；需在 Phase 5 容器或显式可写缓存目录中补验。
- DeepSeek/Embedding 成功调用未在本阶段产生可核验结果；Token usage 也因此没有展示或估算。
- 真实成功回答尚未生成，所以浏览器中的真实引用跳转成功链路未补验；引用组件和后端映射契约有自动化覆盖，但不把测试 Fixture 当成项目端到端效果。
- 前端测试目前是 Node 契约测试，不是完整组件 DOM 测试套件；浏览器已补充核心交互验证，Phase 5 仍需加入可重复运行的端到端测试。
- 当前 `.git` 元数据在基线阶段已确认不可用，无法生成可靠的 Git diff 或提交级 provenance。

Phase 4 到此停止，不自动进入 Phase 5。
