# 执行提示词：完成 InsightFlow 的简历证据验证

请直接接手执行，不要只给建议或把终端、数据库、测试操作交给我。用中文沟通，持续推进到得到可复核结果；遇到真正需要我决定的破坏性操作再询问。

## 我的目标与范围

我准备应聘 AI 应用开发岗位。希望简历中的 InsightFlow 项目能证明我考虑过真实开发中的异常、可靠性、检索质量问题，并采取了措施，而不只是一个正常流程能跑通的 Demo。

这次目标是给现有功能补齐可信证据，最后写出 3～4 条自然、精炼、有成果支撑的简历经历。不是无限加功能，不要自行扩展多租户、权限系统、文档版本管理或重构整个项目。不要使用虚构业务规模、客户、生产落地或未经验证的性能数字。

工作目录：C:\Users\28891\Desktop\WORK\InsightFlow

## 接手前先做

1. 读取适用的 AGENTS.md，检查 git status/diff。当前目录有大量已有未提交修改，包含用户和桌面 Codex 的工作；保留它们，不 reset、覆盖或清理，不默认创建提交或推送。
2. 阅读本文件和下述报告，核对实际代码。历史结果是已有证据，不要当作当前环境仍健康的保证，也不要无理由重复所有已完成工作。
3. 先给一句简短的接手说明，然后实际运行命令。

## 已完成的功能

- PDF 原生文本质量门禁，识别大量 `(cid:数字)` / Unicode 替换字符和空文本。
- 按页本地 OCR 降级：Poppler 渲染，Tesseract chi_sim+eng 识别；正常短文本不因少于 20 字而强制 OCR。
- 单页超时、总预算、OCR 页数上限、置信度检查；逐页来源、耗时和错误保存到 documents.extraction_report。
- 历史坏 PDF 重试时重新解析；全部内容校验通过后替换旧片段，生成新向量，避免旧向量与新内容错配。历史引用快照保留，原 Chunk 外键按已有 SET NULL 处理。
- 永久质量失败不自动重试；BM25/向量只检索 COMPLETED 文档。
- 保留 PDF 空白页边界，修复引用页码偏移。
- 数据库迁移 0005_extraction_report 已在此前 Docker 环境应用。0004_bm25_revision 是已有缓存版本变更，请保留。

重要文件：
- backend/app/parsers/ocr.py、pdf_parser.py、quality.py
- backend/app/services/document_service.py、bm25_service.py、retrieval_service.py
- backend/app/tasks.py、config.py、models/document.py、routers/documents.py
- backend/alembic/versions/0004_bm25_corpus_revision.py、0005_extraction_report.py
- backend/tests/test_pdf_ocr.py、test_document_quality.py、test_bm25_cache.py
- backend/Dockerfile、docker-compose.yml、两份 .env.example

## 已有真实结果与限制

1. SQL语言.pdf（document ID: 74b11f70-9ba9-47a5-a7e3-a093c5694562）此前 13 页原生解析损坏。2026-09-19 经真实 Worker OCR 恢复，约 32.4 秒解析，旧 79 个坏片段替换成 22 个新片段，22 个都有向量，0 个含 CID 占位符。三种检索均能召回。抽查仍有少量标题和 SQL 符号误识别，不能当作 OCR 准确率评测。
2. 24 份正常 PDF 回归未误触发 OCR；后端及评测完整回归 56 项通过。最后中文 OCR 空格规范化后，相关 9 项 OCR 测试再次通过。
3. 2026-09-20 在新的独立 SQLite 数据库上，重新执行持久化、Celery、质量、BM25 缓存相关测试，共 25 项通过。部分外部依赖使用替身，不是生产并发实验。
4. 现有真实语料的探索性检索集有 30 题，三路共 90 次请求：指定证据完整命中 BM25 25/30、Vector 9/30、Hybrid 22/30。标签为 AI 初标，可能漏等价段落；不能称为人工标注、正式 Recall 或回答准确率。该结果在 OCR 修复前产生，语料及 Chunk ID 后来已变化，不能无校验复用冻结哈希。
5. 早期 generated_benchmark 是程序化语料，不可包装成真实业务效果。

先阅读：
- docs/resume-evidence-status.md
- docs/document-quality.md
- docs/ocr-recovery-verification.md
- tmp/ocr_recovery_live.json、tmp/ocr_native_regression.json
- tmp/resume_reliability_20260920.xml
- eval/reports/resume_evidence_20260919/README.md、questions.pending.json、label_review.md、cases.jsonl、summary.json
- eval/reports/generated_benchmark_20260828/AUDIT.md
- eval/README.md、eval/run_eval.py、eval/run_resume_probe.py

部分 docs、tmp、eval/reports 被 Git 忽略，但文件在本地存在，不要只看 git ls-files。

## 按顺序执行的工作

### 一、恢复本地运行环境

2026-09-20 API 127.0.0.1:8000 连接被拒绝。Docker Desktop 原本未运行，桌面 Codex 使用 Start-Process -WindowStyle Hidden 启动后，Docker 后台崩溃。

日志路径：%LOCALAPPDATA%\Docker\log\host\com.docker.backend.exe.log
关键错误：
`starting services: initializing Inference manager: listening on unix://C:/Users/28891/AppData/Local/Docker/run/dockerInference: remove ... dockerInference: The file cannot be accessed by the system. (listener: The filename, directory name, or volume label syntax is incorrect.)`

请查明当前状态并采取可逆、最小必要修复。这是具体启动故障，不要未经验证认定只是 Codex 沙箱权限。不要恢复出厂设置、删除 Docker 卷、注销 WSL 发行版或重装清空数据；若真的必须做，先说明具体对象、影响、备份与恢复方案并请求确认。不得读取或输出 .env 密钥。后台启动窗口用 Hidden。

服务恢复后，核查 Compose、迁移版本、API 与 Worker 健康。现有镜像包含 OCR；不要无理由全量重建。若必须构建，Dockerfile 已使用 HTTPS Debian 源和下载重试，初次 HTTP 源曾多次 502。

### 二、实际验证任务可靠性

已经准备好脚本，但尚未执行：
`scripts/probe_resume_reliability.py`

它创建唯一临时测试文档，测试 20 个并发请求使用相同幂等键、质量失败只执行一次、失败文档三路检索不可见，并仅清理本次测试文档。先审查脚本，再用 `.venv-phase3/Scripts/python.exe` 运行。20 个并发的结果目前不存在，不要预先宣称通过。保留首次 HTTP 状态，初始化期间的 409 与重试后的结果分别报告。

继续用独立测试数据补充：
- 已完成任务多次投递：核查任务、文档、Chunk 数量和 ID，确保无新增重复数据。
- 在解析/向量生成阶段注入可控失败：检查状态、可检索性、重试后数据完整性。优先隔离环境；不要杀死用户正在处理业务的 Worker。
- 验证已有 OCR 修复链路的文本、向量与状态一致性，合理复用已有真实证据。

保存测试输入、真实运行环境、注入位置、API 返回、数据库前后状态及检索结果。严格区分单元测试替身、真实 PostgreSQL/Celery 实验、单次重放、并发或进程中断。不要把“无重复”夸大成 exactly-once 或全故障可恢复。

发现与目标直接相关的实现缺陷可以修复并回归，不要为了凑数字重复简单实验或扩大范围。

### 三、核查检索效果

从已有 30 题继续，不从零造一套有利于方案的题。根据原文检查问题可回答性、必需证据和等价段落；跨文档题需要全部证据。检查漏召回时，不要只给某种模式补相关标签。

记录 AI 标注与人工复核状态。不得自称人工，也不得替用户填 approved_human。已有正式评测入口有人工标签要求，不要绕过。可以完成 AI 辅助证据审查与探索性对照；若仍需人的语义判断，整理为少量“问题＋原文＋你的建议判定”的易读材料，而不是让用户操作数据库。

固定同一语料、Top-K、模型和参数，对比 BM25、Vector、Hybrid。记录产品路径中的阈值、候选配额和多样性差异，不把产品路径对比冒充纯 RRF 消融。若调参，开发题与最终验证题分开；保留不利结果，不挑选题目追求漂亮数字。

指标优先：指定答案证据命中率、跨文档完整证据命中率、检索延迟。未穷尽相关段落不要称 Recall；未评估生成答案不要称回答准确率。分母、错误数、样本量与局限要明确。Hybrid 若不如 BM25，解释具体失败模式，不宣称全面领先。

### 四、交付给我能直接使用的成果

1. 一份中文证据报告：实际测了什么、结果、复现方法、支持什么结论、不能支持什么结论。
2. 可重复运行的脚本和原始结果，旧报告保留，新结果另建日期/版本目录。
3. 一版 3～4 条的中文简历项目经历。突出具体问题、我的措施和可信成果，避免技术说明书风格、技术栈堆砌、空泛的“企业级/高可用/高并发”。不要把 27 份文档的规模强行当亮点。
4. 每条经历配 2～3 句面试解释：为什么这样设计、如何验证、有什么边界。

最终只汇报真正完成的结果。若环境仍阻塞，指出已尝试的可逆措施与具体剩余障碍，同时完成不依赖 Docker 的证据审查和文案工作。不要把“下一步你去……”当作完成任务。
