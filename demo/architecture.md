# InsightFlow 架构

InsightFlow 使用 FastAPI 提供文档、检索、会话、Agent 和评测 API。PostgreSQL 保存文档、Chunk、向量、会话、消息、引用、任务和工作流事件，pgvector 执行向量相似度检索。

文档上传后由 Redis 中的 Celery 队列异步处理。Worker 依次解析文件、按结构切分内容、生成 Embedding，并把 Chunk 写入数据库。任务进度和错误原因写入数据库，重复投递通过幂等键与唯一约束避免重复 Chunk。

检索支持 BM25、pgvector 和两者经过 RRF 融合的混合模式。只有用户明确选择文档集合时才启用跨文档覆盖策略；证据不足时不会强行补入低相关 Chunk。
