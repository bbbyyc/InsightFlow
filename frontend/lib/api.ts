const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export type TaskRecord = {
  id: string; status: "pending" | "running" | "retrying" | "succeeded" | "failed" | "cancelled";
  progress: number; attempt: number; max_retries: number; error_type?: string | null;
  error_message?: string | null; result_summary: Record<string, unknown>;
};

export type DocumentItem = {
  id: string; title: string; file_type: string; status: "pending" | "processing" | "completed" | "failed";
  chunk_count: number; error_message?: string | null; processed_at?: string | null;
  created_at?: string | null; task?: TaskRecord | null;
};

export type DocumentChunk = {
  id: string; content: string; chunk_index: number; page_number: number | null;
  section_title: string | null; token_count: number;
};

export type DocumentDetail = DocumentItem & { chunks: DocumentChunk[] };

export type SearchResult = {
  chunk_id: string; content: string; document_id: string; document_title: string;
  document_type: string; chunk_index: number; page_number: number | null;
  section_title: string | null; score: number; retrieval_channels: string[];
  channel_ranks: Record<string, number>; channel_scores: Record<string, number>;
  rrf_rank: number; fused_rank: number;
};

export type Citation = {
  citation_number: number; document_title: string; document_id: string;
  section_title: string | null; content_snippet: string; chunk_id: string;
};

export type ConversationItem = { id: string; title: string; document_ids: string[]; created_at?: string };
export type ConversationDetail = ConversationItem & { messages: Array<{ id: string; role: "user" | "assistant"; content: string; citations: Citation[]; created_at: string }> };

export type AgentResult = {
  answer: string; citations: Citation[]; iterations: number; search_queries: string[];
  token_usage: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  retrieval_diagnostics: Record<string, unknown>; retrieval_results: SearchResult[];
};

export type AgentEvent = {
  event: "accepted" | "plan" | "search" | "rewrite" | "generate" | "answer_delta" | "complete" | "failed";
  status?: string; duration_ms?: number; summary?: Record<string, unknown>; error?: string;
  run_id?: string; conversation_id?: string; result?: AgentResult;
};

export type EvaluationRun = {
  id: string; task_id: string | null; status: string; dataset_path: string; dataset_sha256: string | null;
  parameters: Record<string, unknown>; output_dir: string; summary: Record<string, any>;
  error_message: string | null; created_at: string | null; completed_at: string | null;
  cases?: Array<Record<string, any>>; failures?: { count: number; failures: Array<Record<string, any>> };
};

async function parseResponse<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `请求失败（HTTP ${res.status}）`);
  }
  return data as T;
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init });
  return parseResponse<T>(res);
}

export async function uploadDocument(file: File) {
  const body = new FormData(); body.append("file", file);
  return jsonRequest<{ id: string; created: boolean; task: TaskRecord }>("/api/documents/upload", {
    method: "POST", body, headers: { "Idempotency-Key": crypto.randomUUID() },
  });
}
export const listDocuments = () => jsonRequest<DocumentItem[]>("/api/documents");
export const getDocument = (id: string) => jsonRequest<DocumentDetail>(`/api/documents/${id}`);
export const deleteDocument = (id: string) => jsonRequest(`/api/documents/${id}`, { method: "DELETE" });
export const retryDocument = (id: string) => jsonRequest<{ document: DocumentItem; task: TaskRecord }>(`/api/documents/${id}/retry`, { method: "POST" });

export async function searchDocuments(query: string, mode: "bm25" | "vector" | "hybrid", documentIds: string[]) {
  return jsonRequest<{ results: SearchResult[]; diagnostics: Record<string, unknown> }>("/api/search", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, mode, top_k: 10, document_ids: documentIds }),
  });
}

export const listConversations = () => jsonRequest<ConversationItem[]>("/api/chat/conversations");
export const getConversation = (id: string) => jsonRequest<ConversationDetail>(`/api/chat/conversations/${id}`);
export const createConversation = (title = "新会话", documentIds: string[] = []) => jsonRequest<ConversationItem>("/api/chat/conversations", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title, document_ids: documentIds }),
});
export const deleteConversation = (id: string) => jsonRequest(`/api/chat/conversations/${id}`, { method: "DELETE" });
export const renameConversation = (id: string, title: string) => jsonRequest(`/api/chat/conversations/${id}`, {
  method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }),
});
export const addDocumentToConversation = (conversationId: string, documentId: string) => jsonRequest(`/api/chat/conversations/${conversationId}/documents`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ document_id: documentId }),
});
export const removeDocumentFromConversation = (conversationId: string, documentId: string) => jsonRequest(`/api/chat/conversations/${conversationId}/documents/${documentId}`, { method: "DELETE" });

export async function streamAgent(
  query: string,
  documentIds: string[],
  conversationId: string | null,
  signal: AbortSignal,
  onEvent: (event: AgentEvent) => void,
) {
  const res = await fetch(`${API_BASE}/api/agent/stream`, {
    method: "POST", signal, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, document_ids: documentIds, conversation_id: conversationId, max_iterations: 2 }),
  });
  if (!res.ok || !res.body) return parseResponse<never>(res);
  const headerRunId = res.headers.get("X-Agent-Run-Id");
  const headerConversationId = res.headers.get("X-Conversation-Id");
  if (headerRunId) onEvent({ event: "accepted", run_id: headerRunId, conversation_id: headerConversationId || undefined });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/); buffer = frames.pop() || "";
    for (const frame of frames) {
      const eventLine = frame.split(/\r?\n/).find(line => line.startsWith("event:"));
      const dataLine = frame.split(/\r?\n/).find(line => line.startsWith("data:"));
      if (!dataLine) continue;
      const payload = JSON.parse(dataLine.slice(5).trim());
      onEvent({ ...payload, event: (eventLine?.slice(6).trim() || payload.event) as AgentEvent["event"] });
    }
    if (done) break;
  }
}

export const cancelAgentRun = (runId: string) => jsonRequest<{ id: string; status: string }>(`/api/agent/runs/${runId}/cancel`, { method: "POST" });

export const listEvaluations = () => jsonRequest<EvaluationRun[]>("/api/evaluations");
export const getEvaluation = (id: string) => jsonRequest<EvaluationRun>(`/api/evaluations/${id}`);
