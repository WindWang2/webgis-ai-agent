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
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(INDEX_KEY);
  } catch {
    return [];
  }
  if (!raw) return [];
  return _sessionsFromRaw(raw);
}

/** 持久化原文 → 会话数组（坏载荷安静降级为空，不抛）。 */
function _sessionsFromRaw(raw: string): IndexedSession[] {
  try {
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

/**
 * 索引统计的 memo（#1745 7）。
 *
 * 调用点在渲染体里（``search-drawer`` 每次渲染都调）：旧实现每次都
 * JSON.parse 整个持久化索引、再逐会话 JSON.stringify 一遍（``chars`` 算了
 * 还没有消费方）—— 每敲一个字符就把整套索引重跑一遍。改为按**持久化原文**
 * 记忆：索引没变时零解析零序列化；原文变化（含其他标签页写入同一 key）
 * 才重算，因此统计不会读到过期值。
 */
let _statsMemo: { raw: string; value: { sessions: number; messages: number; chars: number } } | null =
  null;

export function searchIndexStats(): { sessions: number; messages: number; chars: number } {
  const empty = { sessions: 0, messages: 0, chars: 0 };
  if (typeof localStorage === 'undefined') return empty;
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(INDEX_KEY);
  } catch {
    return empty;
  }
  if (!raw) return empty;
  if (_statsMemo && _statsMemo.raw === raw) return { ..._statsMemo.value };
  const sessions = _sessionsFromRaw(raw);
  const value = {
    sessions: sessions.length,
    messages: sessions.reduce((acc, s) => acc + s.docs.length, 0),
    chars: sessions.reduce((acc, s) => acc + JSON.stringify(s).length, 0),
  };
  _statsMemo = { raw, value };
  return { ...value };
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
  limit: number,
): SearchHit[] {
  const q = query.toLowerCase();
  const out: SearchHit[] = [];
  for (const doc of session.docs) {
    if (out.length >= limit) break;
    const at = doc.text.toLowerCase().indexOf(q);
    const refIdHit = doc.refs.find((r) => r.toLowerCase().includes(q));
    if (at < 0 && !refIdHit) continue;
    // 同一 doc 内已发出的 artifact ref 去重（#1308：文本命中 + ref id 命中双路径）
    const emittedRefs = new Set<string>();
    // 文本命中：产物 ref 随上下文出一条产物命中（同位定位）
    if (at >= 0 && doc.refs.length > 0) {
      for (const ref of doc.refs.slice(0, 3)) {
        if (emittedRefs.has(ref)) continue;
        emittedRefs.add(ref);
        out.push({
          kind: 'artifact',
          sessionId: session.id,
          sessionTitle: session.title,
          messageIndex: doc.messageIndex,
          snippet: makeSnippet(doc.text, at, q.length),
          ref,
        });
        if (out.length >= limit) break;
      }
      if (out.length >= limit) break;
    }
    // ref id 本身命中：产物组直接出（按 ref 检索产物名）
    if (refIdHit && !emittedRefs.has(refIdHit)) {
      emittedRefs.add(refIdHit);
      out.push({
        kind: 'artifact',
        sessionId: session.id,
        sessionTitle: session.title,
        messageIndex: doc.messageIndex,
        snippet: `${refIdHit}（位于第 ${doc.messageIndex + 1} 条消息）`,
        ref: refIdHit,
      });
      if (out.length >= limit) break;
    }
    if (at >= 0) {
      out.push({
        kind: 'message',
        sessionId: session.id,
        sessionTitle: session.title,
        messageIndex: doc.messageIndex,
        role: doc.role,
        snippet: makeSnippet(doc.text, at, q.length),
      });
    }
  }
  return out;
}

/**
 * 三组配额权重（#1727）：消息全文是主检索信号拿最大份额；会话标题与图层组
 * 各占一份保底 —— 任一组都不会被排在前面的组灌满后整组静默消失。
 */
const GROUP_WEIGHTS = { session: 3, message: 5, layer: 2 } as const;

/**
 * 把单一全局 `limit` 拆成三组独立配额（会话标题 / 消息+产物 / 图层）。
 *
 * 旧实现三组共用同一个 `hits.length >= limit` 闸门：会话标题与消息产物依序
 * 灌满后，图层组的循环开头就 break，「当前工作区图层」一次都不跑。配额化后
 * 每组各自计量；三组之和 ≤ limit（总量仍受 limit 兜底），小 limit 下保底
 * 份额让位给消息组（limit<3 时无解）。
 */
function groupQuotas(limit: number): { session: number; message: number; layer: number } {
  const total = Math.max(1, Math.floor(limit));
  const sum = GROUP_WEIGHTS.session + GROUP_WEIGHTS.message + GROUP_WEIGHTS.layer;
  const share = (w: number): number => Math.floor((total * w) / sum);
  const session = Math.min(Math.max(1, share(GROUP_WEIGHTS.session)), Math.max(0, total - 2));
  const layer = Math.min(Math.max(1, share(GROUP_WEIGHTS.layer)), Math.max(0, total - session - 1));
  return { session, message: Math.max(0, total - session - layer), layer };
}

/**
 * 本地搜索：会话标题（子串）+ 消息全文（子串，产物 ref 另立产物组）+
 * 当前工作区图层（由调用方注入）。size<=5000 doc 量级下线性扫描（与命令
 * 面板同一性能契约量级）。
 *
 * 三组各自走 groupQuotas 拆出的独立配额（#1727），详见 groupQuotas 注释。
 */
export function searchLocalIndex(
  query: string,
  sessions: IndexedSession[],
  layers: Array<{ id: string; name: string }> = [],
  limit = 60,
): SearchHit[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const quotas = groupQuotas(limit);
  const sessionHits: SearchHit[] = [];
  for (const s of sessions) {
    if (sessionHits.length >= quotas.session) break;
    if (s.title.toLowerCase().includes(q)) {
      sessionHits.push({ kind: 'session', sessionId: s.id, sessionTitle: s.title, snippet: s.title });
    }
  }
  const messageHits: SearchHit[] = [];
  for (const s of sessions) {
    if (messageHits.length >= quotas.message) break;
    messageHits.push(...findMessageHits(s, q, quotas.message - messageHits.length));
  }
  const layerHits: SearchHit[] = [];
  for (const layer of layers) {
    if (layerHits.length >= quotas.layer) break;
    if (layer.name.toLowerCase().includes(q)) {
      layerHits.push({
        kind: 'layer',
        sessionId: 'current',
        sessionTitle: '当前工作区',
        snippet: layer.name,
        layerId: layer.id,
        layerName: layer.name,
      });
    }
  }
  return [...sessionHits, ...messageHits, ...layerHits];
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
