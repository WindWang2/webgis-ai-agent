/**
 * 知识库（RAG）API — /api/v1/knowledge/*（契约详见 frontend/docs/knowledge-market-recon.md）。
 *
 * 后端事实（UI 不得美化）：
 * - POST /documents 是同步索引：返回即 "completed"，无进度事件/轮询 job，
 *   「解析中」只存在于请求在途层面。
 * - search 的 score 是 FAISS L2 距离：越小越相关，不是归一化相似度。
 * - 后端只收纯文本文档（text/markdown/json）；文件读取在客户端完成。
 * - 删除仅 creator 可操作；先向量后 DB 行；删除比 ≥20% 后端自动压实。
 */
import { apiFetch } from './transport';

/** 后端 ApiResponse<T> 信封（app/models/api_response.py）。 */
interface ApiEnvelope<T> {
  code: string;
  success: boolean;
  message: string;
  data: T | null;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  file_type: string | null;
  chunk_count: number;
  status: string;
  created_at: string | null;
}

export interface KnowledgeDocumentList {
  total: number;
  items: KnowledgeDocument[];
}

export interface KnowledgeSearchHit {
  /** 分块 id（chk_*） */
  id: string;
  document_id: string;
  title: string;
  /** 分块全文（后端无截断/预览字段） */
  content: string;
  file_type: string | null;
  /** FAISS L2 距离，越小越相关 */
  score: number;
}

export interface AddDocumentResult {
  document_id: string;
  chunk_count: number;
  status: string;
}

/** 后端 MAX_CONTENT_LENGTH（app/api/routes/knowledge.py）。 */
export const KNOWLEDGE_MAX_CONTENT_BYTES = 100 * 1024 * 1024;

export const KNOWLEDGE_FILE_TYPES = ['text', 'markdown', 'json'] as const;
export type KnowledgeFileType = (typeof KNOWLEDGE_FILE_TYPES)[number];

/** 客户端可读入的文本扩展名 → file_type；其余返回 null（诚实拒收）。 */
export function knowledgeFileTypeForFileName(name: string): KnowledgeFileType | null {
  const lower = name.toLowerCase();
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) return 'markdown';
  if (lower.endsWith('.json')) return 'json';
  if (lower.endsWith('.txt')) return 'text';
  return null;
}

export async function listKnowledgeDocuments(
  opts: { limit?: number; offset?: number; signal?: AbortSignal } = {},
): Promise<KnowledgeDocumentList> {
  const params = new URLSearchParams({
    limit: String(opts.limit ?? 20),
    offset: String(opts.offset ?? 0),
  });
  const body = await apiFetch<ApiEnvelope<KnowledgeDocumentList>>(
    `/api/v1/knowledge/documents?${params.toString()}`,
    { signal: opts.signal, label: 'Knowledge docs error' },
  );
  if (!body.success || !body.data) throw new Error(body.message || '无法加载知识库文档');
  return body.data;
}

export async function addKnowledgeDocument(
  req: { title: string; content: string; fileType: KnowledgeFileType },
  opts: { signal?: AbortSignal } = {},
): Promise<AddDocumentResult> {
  const body = await apiFetch<ApiEnvelope<AddDocumentResult>>(
    '/api/v1/knowledge/documents',
    {
      method: 'POST',
      body: { title: req.title, content: req.content, file_type: req.fileType },
      signal: opts.signal,
      label: 'Knowledge document add error',
    },
  );
  if (!body.success || !body.data) throw new Error(body.message || '文档索引失败');
  return body.data;
}

export async function searchKnowledge(
  opts: { q: string; topK?: number; documentId?: string | null; signal?: AbortSignal },
): Promise<KnowledgeSearchHit[]> {
  const params = new URLSearchParams({ q: opts.q, top_k: String(opts.topK ?? 5) });
  if (opts.documentId) params.set('document_id', opts.documentId);
  const body = await apiFetch<ApiEnvelope<{ results: KnowledgeSearchHit[] }>>(
    `/api/v1/knowledge/search?${params.toString()}`,
    { signal: opts.signal, label: 'Knowledge search error' },
  );
  if (!body.success || !body.data) throw new Error(body.message || '检索失败');
  return body.data.results;
}

export async function deleteKnowledgeDocument(
  documentId: string,
  opts: { signal?: AbortSignal } = {},
): Promise<void> {
  const body = await apiFetch<ApiEnvelope<null>>(
    `/api/v1/knowledge/document/${encodeURIComponent(documentId)}`,
    { method: 'DELETE', signal: opts.signal, label: 'Knowledge document delete error' },
  );
  if (!body.success) throw new Error(body.message || '删除失败或无权访问该文档');
}
