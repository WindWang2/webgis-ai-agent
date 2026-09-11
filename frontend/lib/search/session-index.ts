'use client';

import { apiFetch } from '@/lib/api/transport';

/**
 * 跨会话搜索 —— client 侧本地索引（ADR-0147）。
 *
 * 契约依据（P0 勘察 §3.2）：后端 GET /chat/sessions 无全文检索参数，transcript
 * 全文只能逐会话拉取。本模块把「按需拉到的会话消息」建成持久化本地索引
 * （localStorage，LRU 按 updatedAt 淘汰，多级体积上限），搜索全在本地完成。
 * 后端全文端点是 PR 协调点；端点就位后本模块可作为缓存层平滑替换。
 */

export interface IndexedSession {
  id: string;
  title: string;
  updatedAt: number;
  /** 每消息一条 doc；文本截断到 TEXT_CAP。 */
  docs: Array<{ messageIndex: number; role: string; text: string; refs: string[] }>;
}

export interface SearchHit {
  kind: 'session' | 'message' | 'artifact' | 'layer';
  sessionId: string;
  sessionTitle: string;
  messageIndex?: number;
  role?: string;
  snippet: string;
  ref?: string;
  layerId?: string;
  layerName?: string;
}

const INDEX_KEY = 'geoagent-cross-session-index-v1';
const MAX_SESSIONS = 20;
const MAX_DOCS_PER_SESSION = 200;
const TEXT_CAP = 2000;
/** 索引总体积预算（序列化后字符数）；超限从最旧会话整会话淘汰。 */
const TOTAL_BUDGET_CHARS = 1_000_000;

const REF_RE = /\bref:(?:chart|table|stats|grid|admin)-[A-Za-z0-9_-]{1,64}\b/g;

// ── 索引构建 ──

export function buildIndexedSession(
  id: string,
  title: string,
  updatedAt: number,
  messages: Array<{ role: string; content: string }>,
): IndexedSession {
  const docs: IndexedSession['docs'] = [];
  const cap = Math.min(messages.length, MAX_DOCS_PER_SESSION);
  for (let i = 0; i < cap; i++) {
    const msg = messages[i];
    const text = (msg.content ?? '').slice(0, TEXT_CAP);
    if (!text) continue;
    docs.push({ messageIndex: i, role: msg.role, text, refs: [...new Set(text.match(REF_RE) ?? [])] });
  }
  return { id, title, updatedAt, docs };
}

// ── 持久化（LRU + 体积预算） ──

export function loadPersistedSessions(): IndexedSession[] {
  if (typeof localStorage === 'undefined') return [];
  try {
    const raw = localStorage.getItem(INDEX_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((s): s is IndexedSession => Boolean(s && typeof s === 'object' && s.id));
  } catch {
    return [];
  }
}

export function persistSessions(sessions: IndexedSession[]): IndexedSession[] {
  let kept = [...sessions]
    // LRU：updatedAt 新者优先，同轮次去重
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, MAX_SESSIONS);
  while (kept.length > 0 && JSON.stringify(kept).length > TOTAL_BUDGET_CHARS) {
    kept = kept.slice(0, kept.length - 1); // 淘汰最旧
  }
  if (typeof localStorage !== 'undefined') {
    try {
      if (kept.length === 0) localStorage.removeItem(INDEX_KEY);
      else localStorage.setItem(INDEX_KEY, JSON.stringify(kept));
    } catch {
      /* 配额/隐私模式：索引降级为会话内态 */
    }
  }
  return kept;
}

export function upsertIndexedSession(session: IndexedSession): IndexedSession[] {
  const rest = loadPersistedSessions().filter((s) => s.id !== session.id);
  return persistSessions([session, ...rest]);
}

export function clearSearchIndex(): void {
  if (typeof localStorage !== 'undefined') localStorage.removeItem(INDEX_KEY);
}

export function searchIndexStats(): { sessions: number; messages: number; chars: number } {
  const sessions = loadPersistedSessions();
  return {
    sessions: sessions.length,
    messages: sessions.reduce((acc, s) => acc + s.docs.length, 0),
    chars: sessions.reduce((acc, s) => acc + JSON.stringify(s).length, 0),
  };
}

// ── 搜索 ──

function makeSnippet(text: string, at: number, len: number): string {
  const start = Math.max(0, at - 60);
  const end = Math.min(text.length, at + len + 60);
  return `${start > 0 ? '…' : ''}${text.slice(start, end)}${end < text.length ? '…' : ''}`;
}

function findMessageHits(
  session: IndexedSession,
  query: string,
  hits: SearchHit[],
  limit: number,
): void {
  const q = query.toLowerCase();
  for (const doc of session.docs) {
    if (hits.length >= limit) return;
    const at = doc.text.toLowerCase().indexOf(q);
    const refIdHit = doc.refs.find((r) => r.toLowerCase().includes(q));
    if (at < 0 && !refIdHit) continue;
    // 文本命中：产物 ref 随上下文出一条产物命中（同位定位）
    if (at >= 0 && doc.refs.length > 0) {
      for (const ref of doc.refs.slice(0, 3)) {
        hits.push({
          kind: 'artifact',
          sessionId: session.id,
          sessionTitle: session.title,
          messageIndex: doc.messageIndex,
          snippet: makeSnippet(doc.text, at, q.length),
          ref,
        });
        if (hits.length >= limit) return;
      }
    }
    // ref id 本身命中：产物组直接出（按 ref 检索产物名）
    if (refIdHit) {
      hits.push({
        kind: 'artifact',
        sessionId: session.id,
        sessionTitle: session.title,
        messageIndex: doc.messageIndex,
        snippet: `${refIdHit}（位于第 ${doc.messageIndex + 1} 条消息）`,
        ref: refIdHit,
      });
      if (hits.length >= limit) return;
    }
    if (at >= 0) {
      hits.push({
        kind: 'message',
        sessionId: session.id,
        sessionTitle: session.title,
        messageIndex: doc.messageIndex,
        role: doc.role,
        snippet: makeSnippet(doc.text, at, q.length),
      });
    }
  }
}

/**
 * 本地搜索：会话标题（子串）+ 消息全文（子串，产物 ref 另立产物组）+
 * 当前工作区图层（由调用方注入）。size<=5000 doc 量级下线性扫描（与命令
 * 面板同一性能契约量级）。
 */
export function searchLocalIndex(
  query: string,
  sessions: IndexedSession[],
  layers: Array<{ id: string; name: string }> = [],
  limit = 60,
): SearchHit[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const hits: SearchHit[] = [];
  for (const s of sessions) {
    if (hits.length >= limit) break;
    if (s.title.toLowerCase().includes(q)) {
      hits.push({ kind: 'session', sessionId: s.id, sessionTitle: s.title, snippet: s.title });
    }
  }
  for (const s of sessions) {
    if (hits.length >= limit) break;
    findMessageHits(s, q, hits, limit);
  }
  for (const layer of layers) {
    if (hits.length >= limit) break;
    if (layer.name.toLowerCase().includes(q)) {
      hits.push({
        kind: 'layer',
        sessionId: 'current',
        sessionTitle: '当前工作区',
        snippet: layer.name,
        layerId: layer.id,
        layerName: layer.name,
      });
    }
  }
  return hits;
}

// ── 索引编排：会话列表 → 逐会话拉取消息建索引（可取消） ──

export interface SessionMeta {
  id: string;
  title: string;
  updatedAt: number;
}

export interface IndexProgress {
  done: number;
  total: number;
  currentTitle?: string;
}

/** 会话列表 → 标准化 meta（updatedAt ms）。 */
export function normalizeSessionList(
  res: { sessions?: Array<{ id: string; title?: string; updatedAt?: number | string }> },
): SessionMeta[] {
  return (res.sessions ?? []).map((s) => ({
    id: String(s.id),
    title: s.title ?? '未命名会话',
    updatedAt: typeof s.updatedAt === 'number' ? s.updatedAt : Number(new Date(s.updatedAt ?? 0).getTime() || 0),
  }));
}

/**
 * 顺序索引最多 `budget` 个最近会话（已缓存且新鲜的跳过）。fetcher 注入便于
 * 测试；signal 取消。逐会话 upsert 持久化（中途取消不丢已完成部分）。
 */
export async function buildIndexFromSessions(
  metas: SessionMeta[],
  fetcher: (sessionId: string) => Promise<{ messages?: Array<{ role: string; content: string }> }>,
  options: { budget?: number; onProgress?: (p: IndexProgress) => void; signal?: AbortSignal } = {},
): Promise<IndexedSession[]> {
  const budget = options.budget ?? 20;
  const targets = metas.slice(0, budget);
  const cached = new Map(loadPersistedSessions().map((s) => [s.id, s]));
  let done = 0;
  for (const meta of targets) {
    if (options.signal?.aborted) break;
    const hit = cached.get(meta.id);
    if (hit && hit.updatedAt >= meta.updatedAt && hit.docs.length > 0) {
      done += 1;
      options.onProgress?.({ done, total: targets.length, currentTitle: meta.title });
      continue;
    }
    options.onProgress?.({ done, total: targets.length, currentTitle: meta.title });
    try {
      const data = await fetcher(meta.id);
      if (options.signal?.aborted) break;
      upsertIndexedSession(buildIndexedSession(meta.id, meta.title, meta.updatedAt, data.messages ?? []));
    } catch {
      // 单会话拉取失败不阻断索引（权限/过期）；该会话不在索引中而已。
    }
    done += 1;
    options.onProgress?.({ done, total: targets.length, currentTitle: meta.title });
  }
  return loadPersistedSessions();
}

/** 默认 fetcher：既有会话消息端点（limit 200 = 后端单页上限）。 */
export function defaultSessionFetcher(sessionId: string): Promise<{ messages?: Array<{ role: string; content: string }> }> {
  return apiFetch(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}?limit=200`, {
    label: 'Cross-session index error',
  });
}
