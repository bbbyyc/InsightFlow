"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  AgentEvent, AgentResult, Citation, ConversationItem, DocumentDetail, DocumentItem,
  EvaluationRun, SearchResult, addDocumentToConversation,
  cancelAgentRun,
  deleteConversation, deleteDocument, getConversation, getDocument, getEvaluation,
  listConversations, listDocuments, listEvaluations, removeDocumentFromConversation,
  retryDocument, searchDocuments, streamAgent, uploadDocument,
} from "@/lib/api";

type View = "assistant" | "search" | "evaluations";
type Message = { role: "user" | "assistant"; content: string; citations?: Citation[]; result?: AgentResult; interrupted?: boolean };
type NodeEvent = AgentEvent & { key: string };

const stages: Record<string, string> = {
  queued: "等待处理", parsing: "解析文档", chunking: "切分内容", embedding: "生成 Embedding", indexing: "写入索引",
};

function statusText(status: string) {
  return ({ pending: "等待中", processing: "处理中", running: "运行中", retrying: "正在重试", completed: "已完成", succeeded: "已完成", failed: "失败", cancelled: "已中断" } as Record<string, string>)[status] || status;
}

function metric(value: unknown) {
  return typeof value === "number" ? value.toFixed(3) : "不可用";
}

function CitationMarkdown({ content, citations, onCitation }: { content: string; citations?: Citation[]; onCitation: (citation: Citation) => void }) {
  const parts = content.split(/(\[\d+\])/g);
  return <div className="prose max-w-none">{parts.map((part, index) => {
    const match = part.match(/^\[(\d+)\]$/);
    const citation = match ? citations?.find(item => item.citation_number === Number(match[1])) : undefined;
    return citation ? <button key={index} className="citation-link" onClick={() => onCitation(citation)}>{part}</button> : <ReactMarkdown key={index}>{part}</ReactMarkdown>;
  })}</div>;
}

function DocumentPanel({ documents, selectedIds, onSelect, onRefresh, onInspect }: {
  documents: DocumentItem[]; selectedIds: string[]; onSelect: (id: string) => void;
  onRefresh: () => Promise<void>; onInspect: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function upload(files: FileList | null) {
    if (!files?.length) return; setBusy(true); setError("");
    try { for (const file of Array.from(files)) await uploadDocument(file); await onRefresh(); }
    catch (exc) { setError(exc instanceof Error ? exc.message : "上传失败"); }
    finally { setBusy(false); }
  }
  async function remove(item: DocumentItem) {
    if (!confirm(`删除“${item.title}”？`)) return;
    try { await deleteDocument(item.id); await onRefresh(); } catch (exc) { setError(exc instanceof Error ? exc.message : "删除失败"); }
  }
  async function retry(item: DocumentItem) {
    try { await retryDocument(item.id); await onRefresh(); } catch (exc) { setError(exc instanceof Error ? exc.message : "重试失败"); }
  }
  return <aside className="right-panel" aria-label="文档库">
    <div className="panel-title"><div><strong>文档库</strong><span>{documents.length} 份资料</span></div><button onClick={onRefresh} aria-label="刷新文档">↻</button></div>
    <label className="upload-zone">
      <input data-testid="file-input" type="file" multiple accept=".pdf,.md,.markdown,.txt" onChange={event => upload(event.target.files)} disabled={busy} />
      <b>{busy ? "正在提交…" : "上传文档"}</b><span>PDF · Markdown · TXT，最大 25MB</span>
    </label>
    {error && <div className="error-box" role="alert">{error}</div>}
    <div className="document-list">{documents.length === 0 && <div className="empty-small">尚未上传文档</div>}
      {documents.map(item => {
        const task = item.task; const stage = String(task?.result_summary?.stage || "queued");
        const progress = item.status === "completed" ? 100 : Math.min(99, task?.progress || 0);
        return <article key={item.id} className={`document-card ${selectedIds.includes(item.id) ? "selected" : ""}`} data-testid="document-card">
          <div className="document-head">
            <button className="check" aria-label={`选择 ${item.title}`} aria-pressed={selectedIds.includes(item.id)} onClick={() => onSelect(item.id)}>{selectedIds.includes(item.id) ? "✓" : ""}</button>
            <button className="doc-name" onClick={() => onInspect(item.id)}>{item.title}</button>
            <button className="danger-ghost" onClick={() => remove(item)} aria-label={`删除 ${item.title}`}>×</button>
          </div>
          <div className="doc-meta"><span className={`status ${item.status}`}>{statusText(item.status)}</span><span>{item.file_type.toUpperCase()} · {item.chunk_count} Chunks</span></div>
          {(item.status === "pending" || item.status === "processing") && <div className="progress-wrap"><div className="progress-label"><span>{task?.status === "succeeded" ? "等待文档状态同步" : stages[stage] || stage}</span><span>{progress}%</span></div><div className="progress"><i style={{ width: `${progress}%` }} /></div></div>}
          {item.status === "failed" && <div className="failure-detail"><span>{item.error_message || task?.error_message || "处理失败，后端未返回原因"}</span><button onClick={() => retry(item)}>重新执行</button></div>}
          {task?.status === "retrying" && <div className="retry-note">第 {task.attempt}/{task.max_retries + 1} 次尝试</div>}
        </article>;
      })}
    </div>
  </aside>;
}

function EvidenceDrawer({ detail, citation, result, onClose }: { detail: DocumentDetail | null; citation: Citation | null; result: SearchResult | null; onClose: () => void }) {
  if (!detail && !citation && !result) return null;
  const chunk = detail?.chunks.find(item => item.id === (citation?.chunk_id || result?.chunk_id));
  const text = result?.content || chunk?.content || citation?.content_snippet;
  return <div className="drawer" role="dialog" aria-label="证据详情">
    <div className="drawer-head"><div><span>证据详情</span><strong>{citation?.document_title || result?.document_title || detail?.title}</strong></div><button onClick={onClose}>×</button></div>
    {(citation || result) && <div className="evidence-meta">
      <span>{citation?.section_title || result?.section_title || "未标注章节"}</span>
      <span>原文片段 #{result?.chunk_index ?? chunk?.chunk_index ?? "—"}</span>
      <span>{(result?.page_number ?? chunk?.page_number) != null ? `第 ${(result?.page_number ?? chunk?.page_number)} 页` : "原文位置：Chunk 索引"}</span>
    </div>}
    {result && <div className="rank-grid"><span>通道 <b>{result.retrieval_channels.join(" + ")}</b></span><span>通道排名 <b>{Object.entries(result.channel_ranks).map(([key, value]) => `${key} #${value}`).join(" · ")}</b></span><span>RRF <b>#{result.rrf_rank}</b></span><span>融合排名 <b>#{result.fused_rank}</b></span></div>}
    {text && <div className="chunk-content"><ReactMarkdown>{text}</ReactMarkdown></div>}
    {detail && !text && <div className="chunk-list">{detail.chunks.map(item => <section key={item.id}><b>Chunk #{item.chunk_index} · {item.section_title || "未标注章节"}{item.page_number != null ? ` · 第 ${item.page_number} 页` : ""}</b><p>{item.content}</p></section>)}</div>}
  </div>;
}

function EvaluationPage() {
  const [runs, setRuns] = useState<EvaluationRun[]>([]); const [selected, setSelected] = useState<EvaluationRun | null>(null); const [loading, setLoading] = useState(true); const [error, setError] = useState("");
  async function load() { setLoading(true); try { const data = await listEvaluations(); setRuns(data); if (data[0]) setSelected(await getEvaluation(data[0].id)); } catch (exc) { setError(exc instanceof Error ? exc.message : "加载失败"); } finally { setLoading(false); } }
  useEffect(() => { load(); }, []);
  const verified = selected?.status === "completed" && selected.summary?.status === "completed" && selected.summary?.metadata?.dataset_purpose === "project_evaluation";
  const metrics = verified ? selected?.summary?.metrics_by_mode : null;
  return <main className="evaluation-page">
    <div className="developer-badge">开发者工具</div>
    <div className="page-heading"><div><span>QUALITY REPORT</span><h2>离线质量评测</h2><p>用于比较不同检索策略并定位失败案例，不属于日常问答流程。这里只展示通过人工 Gold Label 与正式语料门禁的结果。</p></div><button className="secondary-button" onClick={load}>刷新</button></div>
    {loading && <div className="skeleton-card">正在读取评测运行…</div>}{error && <div className="error-box">{error}</div>}
    {!verified && !loading && <div className="verified-empty"><div>∅</div><h3>暂无已验证结果</h3><p>当前没有完成且通过人工 Gold Label 与正式语料门禁的项目评测。页面不会展示 Fixture 数字或历史示例数字。</p><p className="empty-explain">这是只读质量报告，不会因日常提问自动变化；完成人工标注数据集并从评测 API 发起正式运行后，结果才会出现在这里。</p>{runs.length > 0 && <span>数据库中有 {runs.length} 次运行，但均不满足正式展示条件。</span>}</div>}
    {verified && metrics && <>
      <div className="metric-cards">{["bm25", "vector", "hybrid"].map(mode => <article key={mode}><span>{mode === "hybrid" ? "BM25 + VECTOR + RRF" : mode.toUpperCase()}</span><div><b>Recall@5</b><strong>{metric(metrics[mode]?.recall_at_5)}</strong></div><div><b>MRR</b><strong>{metric(metrics[mode]?.mrr)}</strong></div><div><b>nDCG@5</b><strong>{metric(metrics[mode]?.ndcg_at_5)}</strong></div><small>平均 {metric(metrics[mode]?.retrieval_latency?.average_ms)} ms · P50 {metric(metrics[mode]?.retrieval_latency?.p50_ms)} · P95 {metric(metrics[mode]?.retrieval_latency?.p95_ms)}</small><small>Token：{metrics[mode]?.token_usage_status === "available" ? metric(metrics[mode]?.average_total_tokens) : "不可用"}</small></article>)}</div>
      <section className="data-section"><h3>逐题结果</h3><div className="case-table">{selected?.cases?.map((item, index) => <div key={index}><span>{item.case_id}</span><b>{item.mode}</b><p>{item.query}</p><span>Recall {metric(item.metrics?.recall_at_5)}</span><span>{metric(item.retrieval_latency_ms)} ms</span><span>Token {item.token_usage?.status === "available" ? item.token_usage.total_tokens : "不可用"}</span></div>)}</div></section>
      <section className="data-section"><h3>失败 Case</h3>{selected?.failures?.count ? selected.failures.failures.map((failure, index) => <pre key={index}>{JSON.stringify(failure, null, 2)}</pre>) : <p>本次运行没有记录失败 Case。</p>}</section>
    </>}
  </main>;
}

export default function Home() {
  const [view, setView] = useState<View>("assistant"); const [documents, setDocuments] = useState<DocumentItem[]>([]); const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null); const [selectedDocs, setSelectedDocs] = useState<string[]>([]); const [messages, setMessages] = useState<Message[]>([]);
  const [query, setQuery] = useState(""); const [busy, setBusy] = useState(false); const [events, setEvents] = useState<NodeEvent[]>([]); const [searchMode, setSearchMode] = useState<"bm25" | "vector" | "hybrid">("hybrid"); const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [lastSearchQuery, setLastSearchQuery] = useState(""); const [draftAnswer, setDraftAnswer] = useState("");
  const [streamAccepted, setStreamAccepted] = useState(false);
  const [developerOpen, setDeveloperOpen] = useState(false);
  const [detail, setDetail] = useState<DocumentDetail | null>(null); const [citation, setCitation] = useState<Citation | null>(null); const [evidence, setEvidence] = useState<SearchResult | null>(null); const [globalError, setGlobalError] = useState("");
  const abortRef = useRef<AbortController | null>(null); const conversationRef = useRef<HTMLDivElement>(null); const endRef = useRef<HTMLDivElement>(null);
  const runIdRef = useRef<string | null>(null);
  async function loadDocuments() { try { const data = await listDocuments(); setDocuments(data); setSelectedDocs(previous => previous.filter(id => data.some(item => item.id === id))); } catch (exc) { setGlobalError(exc instanceof Error ? exc.message : "文档加载失败"); } }
  async function loadConversations() { try { setConversations(await listConversations()); } catch (exc) { setGlobalError(exc instanceof Error ? exc.message : "会话加载失败"); } }
  useEffect(() => { loadDocuments(); loadConversations(); }, []);
  useEffect(() => { if (!documents.some(item => ["pending", "processing"].includes(item.status) || ["pending", "running", "retrying"].includes(item.task?.status || ""))) return; const timer = setInterval(loadDocuments, 700); return () => clearInterval(timer); }, [documents]);
  useEffect(() => { const container = conversationRef.current; if (container) container.scrollTo({ top: container.scrollHeight, behavior: "smooth" }); }, [messages, events, draftAnswer]);
  const selectedReady = useMemo(() => selectedDocs.every(id => documents.find(item => item.id === id)?.status === "completed"), [selectedDocs, documents]);

  async function selectDocument(id: string) {
    const next = selectedDocs.includes(id) ? selectedDocs.filter(item => item !== id) : [...selectedDocs, id]; setSelectedDocs(next);
    if (conversationId) try { if (next.includes(id)) await addDocumentToConversation(conversationId, id); else await removeDocumentFromConversation(conversationId, id); } catch (exc) { setGlobalError(exc instanceof Error ? exc.message : "会话文档更新失败"); }
  }
  async function inspectDocument(id: string) { try { setDetail(await getDocument(id)); setCitation(null); setEvidence(null); } catch (exc) { setGlobalError(exc instanceof Error ? exc.message : "文档详情加载失败"); } }
  async function selectConversation(item: ConversationItem) { try { const full = await getConversation(item.id); setConversationId(item.id); localStorage.setItem("insightflow:lastConversationId", item.id); setSelectedDocs(full.document_ids); setMessages(full.messages.map(message => ({ role: message.role, content: message.content, citations: message.citations }))); setDraftAnswer(""); setView("assistant"); setEvents([]); } catch (exc) { setGlobalError(exc instanceof Error ? exc.message : "会话恢复失败"); } }
  function newConversation() {
    // A blank conversation is only a local draft. Persist it on the first
    // question so repeated clicks cannot create empty database records.
    if (!conversationId && messages.length === 0) return;
    setConversationId(null); localStorage.removeItem("insightflow:lastConversationId");
    setSelectedDocs([]); setMessages([]); setDraftAnswer(""); setEvents([]);
    setQuery(""); setGlobalError(""); setView("assistant");
  }
  async function removeConversation(id: string) { if (!confirm("删除这条会话？")) return; try { await deleteConversation(id); if (conversationId === id) { setConversationId(null); setMessages([]); localStorage.removeItem("insightflow:lastConversationId"); } await loadConversations(); } catch (exc) { setGlobalError(exc instanceof Error ? exc.message : "删除会话失败"); } }

  useEffect(() => {
    const saved = localStorage.getItem("insightflow:lastConversationId");
    if (!saved) return;
    getConversation(saved).then(full => {
      setConversationId(full.id); setSelectedDocs(full.document_ids);
      setMessages(full.messages.map(message => ({ role: message.role, content: message.content, citations: message.citations })));
    }).catch(() => localStorage.removeItem("insightflow:lastConversationId"));
  }, []);

  async function runSearch(text: string, mode: "bm25" | "vector" | "hybrid") {
    setBusy(true); setGlobalError("");
    try { const data = await searchDocuments(text, mode, selectedDocs); setSearchResults(data.results); }
    catch (exc) { setGlobalError(exc instanceof Error ? exc.message : "检索失败"); }
    finally { setBusy(false); }
  }

  function changeSearchMode(mode: "bm25" | "vector" | "hybrid") {
    setSearchMode(mode);
    if (lastSearchQuery && !busy) runSearch(lastSearchQuery, mode);
  }

  async function send() {
    const text = query.trim(); if (!text || busy) return; setGlobalError(""); setQuery("");
    if (view === "search") { setLastSearchQuery(text); await runSearch(text, searchMode); return; }
    if (selectedDocs.length && !selectedReady) { setGlobalError("所选文档仍在处理或已失败，请等待完成或重新执行。"); return; }
    setMessages(previous => [...previous, { role: "user", content: text }]); setEvents([]); setDraftAnswer(""); setBusy(true); setStreamAccepted(false); const controller = new AbortController(); abortRef.current = controller;
    try {
      await streamAgent(text, selectedDocs, conversationId, controller.signal, event => {
        if (event.event === "accepted") { runIdRef.current = event.run_id || null; setStreamAccepted(Boolean(event.run_id)); if (event.conversation_id) { setConversationId(event.conversation_id); localStorage.setItem("insightflow:lastConversationId", event.conversation_id); } return; }
        if (["plan", "search", "rewrite", "generate"].includes(event.event)) setEvents(previous => [...previous, { ...event, key: `${event.event}-${event.status}-${previous.length}` }]);
        if (event.event === "answer_delta") setDraftAnswer(previous => previous + String(event.summary?.delta || ""));
        if (event.event === "complete" && event.result) { setDraftAnswer(""); setMessages(previous => [...previous, { role: "assistant", content: event.result!.answer, citations: event.result!.citations, result: event.result }]); setBusy(false); loadConversations(); }
        if (event.event === "failed") { setDraftAnswer(""); setMessages(previous => [...previous, { role: "assistant", content: event.status === "cancelled" ? "本轮回答已中断。" : `生成失败：${event.error || "未知错误"}`, interrupted: true }]); setBusy(false); }
      });
    } catch (exc) { if (!controller.signal.aborted) { setGlobalError(exc instanceof Error ? exc.message : "流式回答失败"); setMessages(previous => [...previous, { role: "assistant", content: "回答失败，请检查后端状态后重试。", interrupted: true }]); } }
    finally { setBusy(false); setStreamAccepted(false); abortRef.current = null; runIdRef.current = null; }
  }
  function stop() { const runId = runIdRef.current; if (runId) cancelAgentRun(runId).catch(exc => setGlobalError(exc instanceof Error ? exc.message : "取消状态写入失败")); abortRef.current?.abort(); abortRef.current = null; runIdRef.current = null; setDraftAnswer(""); setBusy(false); setMessages(previous => [...previous, { role: "assistant", content: "本轮回答已由用户中断。", interrupted: true }]); }
  async function openCitation(item: Citation) { setCitation(item); setEvidence(null); try { setDetail(await getDocument(item.document_id)); } catch { setDetail(null); } }
  function openEvidence(item: SearchResult) { setEvidence(item); setCitation(null); setDetail(null); }
  const lastResult = [...messages].reverse().find(item => item.result)?.result;

  return <div className="app-shell">
    <aside className="left-rail">
      <div className="brand"><i>IF</i><div><strong>InsightFlow</strong><span>Evidence workspace</span></div></div>
      <button className="primary-button" onClick={newConversation} disabled={busy}>＋ 新建会话</button>
      <nav>
        <button className={view === "assistant" ? "active" : ""} onClick={() => { setView("assistant"); setDeveloperOpen(false); }}>◫ 研究工作区</button>
        <button className={`developer-toggle ${developerOpen ? "open" : ""}`} aria-expanded={developerOpen} onClick={() => setDeveloperOpen(open => !open)}>⚙ 开发者工具 <span>{developerOpen ? "−" : "+"}</span></button>
        {developerOpen && <div className="developer-nav">
          <button className={view === "search" ? "active" : ""} onClick={() => setView("search")}>⌕ 检索调试</button>
          <button className={view === "evaluations" ? "active" : ""} onClick={() => setView("evaluations")}>▥ 离线评测</button>
        </div>}
      </nav>
      <div className="history-title"><span>历史会话</span><button onClick={loadConversations}>↻</button></div>
      <div className="history-list">{conversations.map(item => <div key={item.id} className={conversationId === item.id ? "active" : ""}><button onClick={() => selectConversation(item)}><b>{item.title}</b><span>{item.document_ids.length} 份文档</span></button><button className="delete-conversation" onClick={() => removeConversation(item.id)}>×</button></div>)}</div>
      <div className="privacy-note"><b>真实数据模式</b><span>任务、引用与指标均来自后端</span></div>
    </aside>
    {view === "evaluations" ? <EvaluationPage /> : <>
      <main className="workspace">
        <header className="workspace-header"><div><span>{view === "search" ? "DEVELOPER · RETRIEVAL DEBUG" : "EVIDENCE-BASED RESEARCH"}</span><h1>{view === "search" ? "检索调试" : "多文档研究助手"}</h1></div><div className="scope-pill">{selectedDocs.length ? `${selectedDocs.length} 份指定文档` : "全局知识库"}</div></header>
        {globalError && <div className="global-error" role="alert"><span>{globalError}</span><button onClick={() => setGlobalError("")}>×</button></div>}
        {view === "assistant" ? <div className="conversation" ref={conversationRef}>
          {messages.length === 0 && <div className="hero"><span>EVIDENCE FIRST</span><h2>让每个结论都能回到原文</h2><p>选择一份或多份资料，InsightFlow 会查找证据、补充检索，并给出带原文出处的回答。</p><div><button onClick={() => setQuery("概括所选文档的核心结论，并注明证据")}>概括核心结论</button><button onClick={() => setQuery("比较这些文档的共同点和分歧")}>跨文档比较</button></div></div>}
          {messages.map((message, index) => <article key={index} className={`message ${message.role} ${message.interrupted ? "interrupted" : ""}`}><div className="avatar">{message.role === "user" ? "YOU" : "IF"}</div><div className="message-body"><span>{message.role === "user" ? "你" : "InsightFlow"}</span><CitationMarkdown content={message.content} citations={message.citations} onCitation={openCitation} />{message.result && <div className="answer-meta"><span>{message.result.citations.length} 条引用</span><span>{message.result.retrieval_results.length} 个召回 Chunk</span><span>Token {message.result.token_usage.total_tokens ?? "不可用"}</span></div>}</div></article>)}
          {draftAnswer && <article className="message assistant streaming-answer"><div className="avatar">IF</div><div className="message-body"><span>InsightFlow · 正在组织回答</span><CitationMarkdown content={draftAnswer} onCitation={openCitation} /><i className="stream-cursor" /></div></article>}
          {(busy || events.length > 0) && <section className="node-timeline" aria-label="回答进度"><div className="timeline-title"><span>回答进度</span><b>{busy ? "执行中" : "已完成"}</b></div>{events.map(item => <div key={item.key} className={`node ${item.status}`}><i /><strong>{({ plan: "理解问题", search: "查找资料", rewrite: "补充证据", generate: "组织回答" } as Record<string, string>)[item.event] || item.event}</strong><span>{statusText(item.status || "")}</span><small>{item.duration_ms != null ? `${item.duration_ms.toFixed(0)} ms` : "—"}</small>{item.error && <p>{item.error}</p>}</div>)}</section>}
          {lastResult?.retrieval_results?.length ? <details className="retrieval-strip"><summary><div><h3>检索与证据详情</h3><span>{lastResult.retrieval_results.length} 条候选证据 · 展开查看系统如何找到这些内容</span></div><b>查看详情</b></summary><div className="retrieval-grid">{lastResult.retrieval_results.map(item => <button key={item.chunk_id} onClick={() => openEvidence(item)}><span>{item.document_title}</span><b>{item.section_title || `原文片段 ${item.chunk_index + 1}`}</b><small>{item.retrieval_channels.includes("bm25") && item.retrieval_channels.includes("vector") ? "关键词与语义共同命中" : item.retrieval_channels.includes("bm25") ? "关键词命中" : "语义命中"} · 综合排序第 {item.fused_rank}</small><p>{item.content.slice(0, 120)}</p></button>)}</div></details> : null}
          <div ref={endRef} />
        </div> : <div className="search-space"><div className="developer-badge">开发者工具 · 不影响日常问答</div><div className="mode-switch">{(["bm25", "vector", "hybrid"] as const).map(mode => <button key={mode} className={searchMode === mode ? "active" : ""} onClick={() => changeSearchMode(mode)}>{mode === "hybrid" ? "混合 + RRF" : mode.toUpperCase()}</button>)}</div>{lastSearchQuery && <p className="search-context">当前查询：{lastSearchQuery} · 切换策略后会自动重新检索{selectedDocs.length === 1 && documents.find(item => item.id === selectedDocs[0])?.chunk_count === 1 ? "；所选文档只有 1 个片段，三种结果可能完全相同" : ""}</p>}{searchResults.length === 0 && <div className="search-empty"><h2>检查系统实际召回的原文片段</h2><p>输入问题后切换检索策略，系统会自动重新执行查询。该页面只返回候选证据，不调用大模型生成答案。</p></div>}<div className="search-result-list">{searchResults.map(item => <button key={item.chunk_id} onClick={() => openEvidence(item)}><div><span>#{item.fused_rank}</span><b>{item.document_title}</b><small>{item.section_title || `Chunk #${item.chunk_index}`}</small></div><p>{item.content}</p><footer><span>{item.retrieval_channels.join(" + ")}</span><span>{Object.entries(item.channel_ranks).map(([key, value]) => `${key} #${value}`).join(" · ")}</span><span>RRF #{item.rrf_rank}</span></footer></button>)}</div></div>}
        <div className="composer"><textarea value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }} placeholder={view === "search" ? "输入要检索的关键词或问题…" : "询问文档中的事实、观点或差异…"} />{busy ? (streamAccepted ? <button className="stop-button" onClick={stop}>■ 中断</button> : <button className="stop-button" disabled>连接中…</button>) : <button className="send-button" onClick={send} disabled={!query.trim()}>发送</button>}<span>Enter 发送 · Shift+Enter 换行</span></div>
      </main>
      <DocumentPanel documents={documents} selectedIds={selectedDocs} onSelect={selectDocument} onRefresh={loadDocuments} onInspect={inspectDocument} />
    </>}
    <EvidenceDrawer detail={detail} citation={citation} result={evidence} onClose={() => { setDetail(null); setCitation(null); setEvidence(null); }} />
  </div>;
}
