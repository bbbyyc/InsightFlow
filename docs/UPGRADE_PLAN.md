# InsightFlow Phase 1—5 升级计划

本计划以 `docs/BASELINE_AUDIT.md` 的事实为基线。原则是先固定检索语义，再建立可复现测量，随后调整持久化、产品界面与部署；每个 Phase 都必须以自动化证据收口，避免后续阶段掩盖前一阶段回归。

## 总体依赖顺序

`Phase 1 跨文档检索` → `Phase 2 可复现评测` → `Phase 3 后端与持久化` → `Phase 4 前端产品化` → `Phase 5 Docker/E2E/交付`

Phase 2 的 fixture 可以在 Phase 1 同步设计，但基准结果必须在 Phase 1 检索契约确定后冻结。Phase 4 可做不依赖 API 变更的视觉准备，但正式联调必须等待 Phase 3 API/schema 稳定。

## Phase 1：跨文档检索修正

状态：**已完成（2026-08-13）**。实现与实测记录见 `docs/PHASE1_RESULT.md`。离线检索契约测试 9/9 通过；真实 PostgreSQL/pgvector、FastAPI 和 Docker 集成验证因本机 Docker daemon 不可用而列为 Phase 5 前置补验项。

目标：定义并实现一致、可解释的多文档候选召回、融合、截断和引用上下文策略；移除“每篇文档强制补片段”对全局相关性的破坏。

文件范围：

- `backend/app/services/retrieval_service.py`
- `backend/app/services/bm25_service.py`
- `backend/app/services/hybrid_service.py`
- `backend/app/services/rag_service.py`
- `backend/app/routers/search.py`
- `backend/app/routers/chat.py`
- `backend/app/services/agent_service.py`
- `backend/tests/`（新增检索、跨文档、引用测试）

实施顺序：

1. 写清搜索契约：`document_ids` 是允许集合，不是每篇必选；`top_k` 是最终硬上限；threshold 的适用层级和 score 语义固定。
2. 统一 vector/BM25 候选池策略，采用集合内全局召回；如需多样性，作为显式、可配置的 rerank/diversification 阶段，并保留原始排名证据。
3. RRF 输出保留各路 rank/score 与融合分，确定稳定 tie-break。
4. 删除 chat 的 rerank 后强制补齐逻辑；chat/search/agent/MCP 共享同一检索入口。
5. 引用映射绑定不可变 context index 与 chunk id，覆盖重复 chunk、稀疏引用、非法编号和多文档同名。

验收标准：

- 两篇以上文档 fixture 中，结果严格属于过滤集合、无重复 chunk、数量 `<= top_k`。
- 单一高相关文档可以合法占据多个名次；无关文档不会因“覆盖”被强塞进上下文。
- vector、BM25、hybrid 与 chat/agent 使用一致过滤语义。
- RRF、阈值、tie-break、跨文档排序、引用映射均有确定性测试。
- 不调用 DeepSeek 的测试可离线通过。

完成结果：显式文档范围启用逐文档候选配额和软多样性；未指定范围保持全局 hybrid；移除聊天的无条件文档补齐；新增证据不足诊断、召回通道/通道排名/RRF 排名/最终排名元数据和稳定 Chunk ID 去重。Phase 1 已停止，不自动进入 Phase 2。

## Phase 2：可复现评测

状态：**评测框架与候选集已完成（2026-08-13），正式项目效果评测等待人工 Gold Label 和冻结语料**。实现、命令与本次实测见 `docs/PHASE2_RESULT.md`。默认正式命令已验证会拒绝 `pending_review` 数据；合成 Fixture 只用于公式/产物管线自测。

目标：让任何干净环境都能从版本化语料构建测试库、运行检索评测并得到可比较报告；将外部 LLM 评分与确定性检索/引用评分分离。

文件范围：

- `eval/test_dataset.json`（重构 schema）
- `eval/fixtures/`（新增版本化多文档语料与 ground truth）
- `eval/ingest_fixture.py`（新增）
- `eval/run_eval.py`
- `eval/metrics.py`
- `eval/test_metrics.py`
- `backend/tests/`（共享 fixture/契约测试）
- README 与评测说明文档

依赖：Phase 1 的检索契约和 score 输出。

实施顺序：

1. 建立至少三类语料：单文档、多文档互补、多文档冲突/干扰；使用稳定 document/chunk ground-truth id。
2. 提供幂等清库/导入或独立测试 schema，记录语料 hash、embedding 模型、代码版本和参数。
3. 将检索评测（Recall/MRR/nDCG、过滤正确性、跨文档覆盖）与生成评测分开；默认离线模式不调用付费 API。
4. LLM judge 作为可选层，保存原始请求元数据与响应，并支持重放；不能作为唯一 pass/fail 门禁。
5. 报告 schema 版本化，旧报告不可冒充当前结果。

验收标准：

- 一条命令从空测试库导入 fixture 并生成 JSON 报告。
- 相同代码/fixture/模型缓存下重复两次，确定性指标完全一致。
- 报告包含 git/source 标识（若仓库修复）、数据 hash、配置、模式、逐题排名和汇总。
- CI 离线门禁覆盖 BM25/RRF/引用；外部模型评测显式 opt-in。
- 至少包含跨文档问题、不可回答问题和错误引用检测。

完成结果：统一三方案命令、数据/语料门禁、指标公式、逐题 JSONL、汇总 JSON、对比 CSV、Markdown 报告、失败分类、LLM Judge provenance 与真实 API usage 规则均已实现。当前没有可靠人工 Gold，因此未生成或宣称项目 Recall/MRR/nDCG；需人工确认 `eval/datasets/candidate_project_v1.json` 和 `eval/corpus_manifest.pending.json` 后才能完成正式实验。Phase 2 到此停止，不自动进入 Phase 3。

## Phase 3：后端与持久化

状态：**已完成可在当前环境验证的实现（2026-08-13）**。实现与实测证据见 `docs/PHASE3_RESULT.md`。SQLite 验证数据库上的迁移升级、回滚、再升级与 schema 漂移检查通过；后端、API、会话、SSE、Celery 任务函数、幂等和上传到问答集成测试共 30 项通过。由于本机 Docker daemon 不可用，真实 PostgreSQL/pgvector、Redis broker 与独立 Celery worker 进程联调仍是 Phase 5 前置补验项，不宣称已验证。

目标：建立正式 schema 生命周期、可靠异步处理、明确 API 错误模型与可观测状态。

文件范围：

- `backend/alembic.ini`、`backend/alembic/`（新增）
- `backend/app/models/*.py`
- `backend/app/database.py`、`backend/app/main.py`
- `backend/app/tasks.py`
- `backend/app/services/document_service.py`、检索服务
- `backend/app/routers/*.py`
- `backend/app/config.py`、`.env.example`
- 后端集成测试

依赖：Phase 2 的回归基准，避免迁移/索引变更导致静默质量下降。

实施顺序：

1. 从当前 ORM 生成并人工核验 Alembic 基线；把 extension、列和 HNSW 索引迁入版本化 migration，移除启动时 DDL。
2. 增加必要唯一约束/索引和 embedding 模型、维度兼容策略。
3. 设计文档处理状态、失败原因、重试次数与幂等键；配置 Celery retry/timeout/ack，处理孤儿文件和部分提交。
4. 修正 BM25 生命周期（数据库全文检索或可共享、可失效的索引方案），明确 worker/API 一致性。
5. 加输入 UUID/资源存在校验、统一错误响应、健康/就绪检查（DB、Redis、worker/模型状态分层）。
6. 安全化配置：生产 secrets 外置、CORS/端口按环境、日志脱敏。

验收标准：

- 空库 `upgrade head` 可启动；已有基线库可无损升级；migration 可在测试环境回滚/再升级。
- 上传任务失败可诊断且按策略重试，不产生重复 chunk/关联；删除同步清理 DB 与文件。
- API 集成测试覆盖正常、无效 UUID、不存在资源、超限文件、任务失败、并发关联。
- readiness 在 DB/Redis 不可用时失败，liveness 不被外部模型短暂故障错误拖死。
- Phase 2 检索指标不低于约定阈值。

完成结果：新增两段 Alembic 迁移并移除应用启动时 DDL；持久化文档/Chunk/会话/消息/任务/检索/引用/Agent 节点事件/评测运行；上传和批量评测使用带数据库任务记录、有限重试、错误记录和幂等键的 Celery 任务；聊天及 Agent 支持会话续接；SSE 输出 accepted、plan、search、rewrite、generate、complete/failed 等事件且只保存输入/结果摘要，不保存内部思维链；增加数据库 readiness。当前环境已完成 SQLite 真实 ORM/API 验证与 Uvicorn 启动验证，未进入 Phase 4。

## Phase 4：前端产品化

状态：**已完成当前环境内可验证的实现（2026-08-13）**。实现、命令和浏览器证据见 `docs/PHASE4_RESULT.md`。TypeScript、3 项前端契约测试、生产构建和 31 项后端回归测试通过；真实会话刷新恢复、SSE 失败状态、显式中断及数据库 `cancelled` 持久化、评测空状态和 Redis 不可用任务失败展示已在浏览器验证。当前机器缺少可用 Docker daemon、Redis/Celery worker 和 PostgreSQL/pgvector，且本机 FastEmbed 缓存路径权限失败，因此上传处理成功、真实检索/回答/引用成功链路留待 Phase 5 完整栈补验；项目尚未配置 ESLint，`npm run lint` 仍为交互式配置提示。

目标：把当前功能丰富的单页原型收敛为有可靠状态、错误反馈、可访问性和可测试性的产品界面。

文件范围：

- `frontend/app/page.tsx`（拆分）
- `frontend/components/`（会话、知识库、消息、引用、搜索、状态组件）
- `frontend/lib/api.ts`（typed client/error model）
- `frontend/app/globals.css`
- 前端测试与 E2E selector
- 必要的 Next.js 配置与环境示例

依赖：Phase 3 稳定 API、任务状态与错误契约。

实施顺序：

1. 拆分页面组件与 hooks，统一请求取消、loading/error/empty/retry 状态。
2. 文档上传显示队列、处理进度、失败原因和重试；避免仅以轮询/乐观状态掩盖失败。
3. 清晰区分 grounded/hybrid/general，展示实际使用文档与引用定位；搜索结果显示融合证据但不暴露误导性统一 score。
4. 完善移动端、键盘操作、焦点管理、ARIA、颜色对比与删除确认。
5. 建立组件/交互测试，去除未使用组件或将其纳入统一实现。

验收标准：

- build、typecheck、lint、前端测试全部通过。
- 上传→处理→可检索、会话文档增删、聊天取消/重试、引用打开、删除确认均有自动化覆盖。
- API/worker 失败在界面给出可行动信息，不出现永久“处理中”。
- 主流程键盘可用，移动与桌面关键布局通过视觉/E2E 检查。

## Phase 5：Docker、端到端验收和最终交付

状态：**工程化实现与当前环境可执行验收已完成（2026-08-13），完整容器成功链路受系统权限、模型网络与 Gold Label 阻塞**。详细事实、命令、矩阵及简历描述见 `docs/FINAL_ACCEPTANCE.md`。Compose 配置、迁移顺序、健康检查、非 root 镜像、环境示例、Demo/验收脚本和部署文档已完成；干净 SQLite 迁移、31 项后端回归、前端 lint/typecheck/test/build 和宿主浏览器会话/评测空状态通过。Docker Desktop 因日志目录和 engine pipe permission denied 无法启动；FastEmbed 因模型源不可访问无法下载；项目 Gold 仍为 `pending_review`，故没有容器 E2E 成功或正式项目评测数字。本阶段到此停止，不虚构通过项。

目标：从干净机器以文档化命令启动完整栈，并用真实容器、真实队列、真实数据库完成端到端验收。

文件范围：

- `docker-compose.yml`，可选 `docker-compose.test.yml`
- `backend/Dockerfile`、`frontend/Dockerfile`、`.dockerignore`
- `start.ps1` 与跨平台启动/检查脚本
- `tests/e2e/`、健康检查脚本
- `README.md`、部署/运维/验收文档
- CI 配置与最终基线报告

依赖：Phase 1—4 全部完成。

实施顺序：

1. 增加镜像 build context 排除、非 root 用户、健康检查、明确启动/迁移顺序和可重复模型缓存策略。
2. 去除生产默认明文凭据与不必要宿主端口，提供 dev/test/prod 配置边界。
3. 一键流程执行 migration → 服务启动 → readiness → fixture 导入 → E2E → 评测。
4. 验证 worker 消费、MCP 工具、前后端代理、持久卷重启恢复和失败恢复。
5. 更新 README，记录依赖版本、密钥要求、费用边界、启动/停止/清理与故障排查。

验收标准：

- 干净环境按 README 一条主命令启动，所有容器 healthy；无需手工进容器修复。
- E2E 完成至少：上传两文档、等待处理、跨文档搜索、grounded chat、引用映射、会话恢复、删除清理、MCP search。
- 容器重启后数据一致；worker/Redis/模型 API 的受控故障有预期状态和恢复路径。
- Phase 2 最终报告由本次交付重新生成并带完整 provenance；不存在沿用旧报告。
- secret scan、依赖/镜像漏洞检查、后端测试、前端构建/测试、E2E 全部达到约定门槛。

## 跨阶段风险与门禁

- **先修 Git 工作区**：当前 `.git` 不可用。在进入实施阶段前应确认正确仓库来源并恢复版本历史，否则无法可靠审查差异、密钥历史和生成报告 provenance。
- **不要直接改变 embedding 维度**：模型、ORM Vector(384)、现有数据和 HNSW 索引必须作为一次迁移处理。
- **评测不等于 LLM 打分**：确定性检索/引用门禁必须独立存在，LLM judge 只能补充。
- **外部调用显式化**：DeepSeek 评测会产生网络依赖和费用，应由环境开关启用。
- **每阶段停止点**：各 Phase 通过其验收标准并更新事实文档后才进入下一阶段；Phase 0 不包含任何上述功能修改。
