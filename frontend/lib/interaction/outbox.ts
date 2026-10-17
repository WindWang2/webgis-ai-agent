/**
 * Interaction IR V1 —— 浏览器端可靠 outbox（ADR-0224 C）。
 *
 * 与 map-action-acks.ts 同族的工程范式（debounce 批量/重试/幂等分类），
 * 服务**用户交互事件**方向，新增：
 *
 * - per-tab 稳定身份：clientInstanceId 存 sessionStorage（刷新延续，关页即
 *   失效 —— 多标签页天然独立实例、独立 client_seq）；
 * - 单调 client_seq：计数器存 sessionStorage（仅一个数字，无队列持久化 ——
 *   刷新不回卷序号，不产生 localStorage 无界增长）；
 * - replace_pending 合并：viewport_settled 等高频 kind 在队列中只保留最新
 *   待发条目（风暴合并）；
 * - 水印同步：ack 信封的 watermarks 用于裁剪队列中已被服务端记账的事件
 *   （ack 丢失后的重试不会无限重发）；
 * - pagehide best-effort flush：fetch keepalive（批 ≤50 事件 ≈ 15KB，远离
 *   64KB cap；与 acks 不同，本通道载荷小，keepalive 可用）。
 *
 * sendBeacon 不用（无法携带 X-Session-Token/Bearer —— 与 acks 同约束）。
 * 事件是 fire-and-forget 遥测：队列有界（溢出丢最旧 + 计数披露），任何
 * 发送失败绝不阻塞地图/对话交互。
 */
import { API_BASE } from '@/lib/api/config';
import { getAccessToken } from '@/lib/auth/tokenStore';
import { devOnly } from '@/lib/utils/logger';
import {
  KIND_COALESCE_POLICY,
  MAX_EVENTS_PER_BATCH,
  type InteractionEventV1,
  type InteractionIngestResponse,
} from './contract';

export interface InteractionOutboxOptions {
  /** 当前会话与匿名 owner token（POST 时解析；与 mutation 游标同源）。 */
  getSessionId: () => string | undefined;
  getOwnerToken: (sessionId?: string) => string | null;
  /** Debounce 窗口（默认 500ms）。 */
  debounceMs?: number;
  /** 单事件最大尝试次数（初始 + 重试）。 */
  maxAttempts?: number;
  /** 内存队列上界（溢出丢最旧 + 披露）。 */
  maxQueue?: number;
  retryBaseMs?: number;
  retryMaxMs?: number;
  requestTimeoutMs?: number;
  /** 测试缝：jitter / now / fetch。 */
  random?: () => number;
  now?: () => number;
  fetchImpl?: typeof fetch;
  /** 测试缝：sessionStorage 替身。 */
  storage?: Pick<Storage, 'getItem' | 'setItem'> | null;
}

export interface InteractionOutbox {
  /** 入队一条事件（铸造 event_id/client_seq 在 bus 层完成）。 */
  enqueue: (event: InteractionEventV1) => void;
  /** 立即冲刷（会话切换 / 卸载）。 */
  flush: () => void;
  /** pagehide best-effort 冲刷（keepalive；超出单请求预算的部分放弃）。 */
  flushKeepalive: () => void;
  /** 丢弃队列并取消计时器（进程内不再发）。 */
  dispose: () => void;
  /** 服务端已记账的本地实例水印（诊断/测试）。 */
  acknowledgedSeq: () => number;
}

const DEFAULT_DEBOUNCE_MS = 500;
export const DEFAULT_MAX_ATTEMPTS = 3;
export const DEFAULT_MAX_QUEUE = 256;
const DEFAULT_RETRY_BASE_MS = 250;
const DEFAULT_RETRY_MAX_MS = 4000;
const DEFAULT_REQUEST_TIMEOUT_MS = 10_000;
const CLIENT_ID_KEY = 'intir:client-id';
const CLIENT_SEQ_KEY = 'intir:client-seq';

interface QueuedEvent {
  sessionId: string | undefined;
  token: string | null;
  event: InteractionEventV1;
  attempts: number;
  readyAt: number;
}

function isTimeoutError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const name = (error as { name?: string }).name;
  return name === 'AbortError' || name === 'TimeoutError' || name === 'ApiTimeoutError';
}

function classifyDelivery(input: {
  error?: unknown;
  status?: number;
}): { transient: boolean; permanent: boolean } {
  if (input.error !== undefined) return { transient: true, permanent: false };
  const status = input.status;
  if (status === 408 || status === 429) return { transient: true, permanent: false };
  if (typeof status === 'number' && status >= 500) return { transient: true, permanent: false };
  if (typeof status === 'number' && status >= 400) return { transient: false, permanent: true };
  return { transient: false, permanent: false };
}

function retryDelayMs(
  attemptsAfterFail: number,
  base: number,
  max: number,
  random: () => number,
): number {
  const exp = Math.min(base * 2 ** Math.max(0, attemptsAfterFail - 1), max);
  return Math.floor(exp * (0.5 + random()));
}

function resolveStorage(
  override?: InteractionOutboxOptions['storage'],
): Pick<Storage, 'getItem' | 'setItem'> | null {
  if (override !== undefined) return override;
  try {
    if (typeof window === 'undefined' || !window.sessionStorage) return null;
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** per-tab 稳定实例 id（sessionStorage；不可用时退化到内存态）。 */
export function getClientInstanceId(
  storage?: InteractionOutboxOptions['storage'],
): string {
  const store = resolveStorage(storage);
  if (!store) return `intir-mem-${Math.random().toString(36).slice(2, 10)}`;
  try {
    const existing = store.getItem(CLIENT_ID_KEY);
    if (existing && /^[A-Za-z0-9._:-]{8,64}$/.test(existing)) return existing;
    const c = typeof crypto !== 'undefined' ? crypto : undefined;
    const id = c && typeof c.randomUUID === 'function'
      ? c.randomUUID()
      : `intir-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    store.setItem(CLIENT_ID_KEY, id);
    return id;
  } catch {
    return `intir-mem-${Math.random().toString(36).slice(2, 10)}`;
  }
}

/**
 * 读取并推进 per-tab 单调 seq（持久化仅一个数字）。
 * 测试/回放可传 override 与显式初始值。
 */
export function nextClientSeq(
  storage?: InteractionOutboxOptions['storage'],
  initialFrom?: number,
): number {
  const store = resolveStorage(storage);
  try {
    const raw = store?.getItem(CLIENT_SEQ_KEY);
    const parsed = raw != null && /^\d+$/.test(raw) ? Number.parseInt(raw, 10) : 0;
    const base = initialFrom ?? parsed;
    const next = Math.max(0, base) + 1;
    store?.setItem(CLIENT_SEQ_KEY, String(next));
    return next;
  } catch {
    return (initialFrom ?? 0) + 1;
  }
}

export function createInteractionOutbox(options: InteractionOutboxOptions): InteractionOutbox {
  const {
    getSessionId,
    getOwnerToken,
    debounceMs = DEFAULT_DEBOUNCE_MS,
    maxAttempts = DEFAULT_MAX_ATTEMPTS,
    maxQueue = DEFAULT_MAX_QUEUE,
    retryBaseMs = DEFAULT_RETRY_BASE_MS,
    retryMaxMs = DEFAULT_RETRY_MAX_MS,
    requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
    random = Math.random,
    now = () => Date.now(),
    fetchImpl,
    storage,
  } = options;

  const instanceId = getClientInstanceId(storage);
  let queue: QueuedEvent[] = [];
  const inFlight = new Set<string>();
  const pendingTimers = new Set<ReturnType<typeof setTimeout>>();
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let generation = 0;
  let ackedWatermark = 0;
  let overflowDropped = 0;

  const logEvent = (event: string, fields: Record<string, unknown>): void => {
    devOnly.warn(`[interaction-outbox] ${event}`, fields);
  };

  const trackTimer = (id: ReturnType<typeof setTimeout>): ReturnType<typeof setTimeout> => {
    pendingTimers.add(id);
    return id;
  };

  const untrackTimer = (id: ReturnType<typeof setTimeout> | null): void => {
    if (id === null) return;
    clearTimeout(id);
    pendingTimers.delete(id);
  };

  const coalescePolicyOf = (event: InteractionEventV1): string =>
    KIND_COALESCE_POLICY[event.kind] ?? 'none';

  const enqueue = (event: InteractionEventV1): void => {
    // replace_pending：同 kind 未发条目被本条取代（风暴合并 —— 500 次
    // pan/zoom 只发最后 settled 态）。
    if (coalescePolicyOf(event) === 'replace_pending') {
      const before = queue.length;
      queue = queue.filter(
        (item) => !(item.event.kind === event.kind && item.sessionId === (getSessionId() ?? item.sessionId)),
      );
      if (before !== queue.length) {
        logEvent('coalesced pending', { kind: event.kind, dropped: before - queue.length });
      }
    }
    if (queue.length >= maxQueue) {
      queue.shift();
      overflowDropped += 1;
      logEvent('queue overflow', { dropped: 1, queueSize: queue.length, total: overflowDropped });
    }
    queue.push({
      sessionId: getSessionId(),
      token: getOwnerToken(getSessionId()),
      event,
      attempts: 0,
      readyAt: now(),
    });
    scheduleDebounce();
  };

  const scheduleDebounce = (): void => {
    untrackTimer(debounceTimer);
    debounceTimer = trackTimer(setTimeout(() => {
      debounceTimer = null;
      flushInternal(false);
    }, debounceMs));
  };

  const scheduleRetry = (): void => {
    if (retryTimer !== null) return;
    const nowTs = now();
    let nextAt = Number.POSITIVE_INFINITY;
    for (const item of queue) {
      if (item.readyAt > nowTs && item.readyAt < nextAt) nextAt = item.readyAt;
    }
    if (nextAt === Number.POSITIVE_INFINITY) return;
    retryTimer = trackTimer(setTimeout(() => {
      retryTimer = null;
      flushInternal(false);
    }, Math.max(0, nextAt - now())));
  };

  const applyWatermarks = (response: InteractionIngestResponse): void => {
    const mark = response.watermarks?.[instanceId];
    if (typeof mark === 'number' && mark > ackedWatermark) {
      ackedWatermark = mark;
      // 服务端已记账的旧事件不再重发（ack 丢失重试的收敛面）。
      queue = queue.filter((item) => item.event.client_seq > ackedWatermark);
    }
    if (response.dropped && response.dropped > 0) {
      logEvent('server ledger dropped (disclosure)', { dropped: response.dropped });
    }
  };

  const settleBatch = (
    items: QueuedEvent[],
    classified: { transient: boolean; permanent: boolean },
    gen: number,
  ): void => {
    for (const item of items) inFlight.delete(item.event.event_id);
    if (gen !== generation) return;
    if (!classified.transient && !classified.permanent) return;
    if (classified.permanent) {
      logEvent('permanent drop', { count: items.length });
      return;
    }
    const retryable: QueuedEvent[] = [];
    for (const item of items) {
      item.attempts += 1;
      if (item.attempts >= maxAttempts) {
        logEvent('exhausted retry', { attempts: item.attempts });
      } else {
        item.readyAt = now() + retryDelayMs(item.attempts, retryBaseMs, retryMaxMs, random);
        retryable.push(item);
      }
    }
    if (retryable.length === 0) return;
    for (const item of retryable) queue.push(item);
    scheduleRetry();
  };

  const buildHeaders = (token: string | null): Record<string, string> => {
    const accessToken = getAccessToken();
    return {
      'Content-Type': 'application/json',
      ...(token ? { 'X-Session-Token': token } : {}),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    };
  };

  const postBatch = (
    sessionId: string,
    token: string | null,
    items: QueuedEvent[],
    keepalive = false,
  ): void => {
    const gen = generation;
    for (const item of items) inFlight.add(item.event.event_id);
    const doFetch = fetchImpl ?? fetch;
    const events = items.map((item) => item.event);
    const controller = new AbortController();
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    if (requestTimeoutMs > 0 && !keepalive) {
      timeoutId = trackTimer(setTimeout(() => controller.abort(), requestTimeoutMs));
    }
    const finish = (): void => {
      untrackTimer(timeoutId);
      timeoutId = null;
    };

    doFetch(
      `${API_BASE}/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/interaction-events`,
      {
        method: 'POST',
        headers: buildHeaders(token),
        body: JSON.stringify({ events }),
        signal: controller.signal,
        ...(keepalive ? { keepalive: true } : {}),
      },
    ).then(async (response) => {
      let body: InteractionIngestResponse | undefined;
      try {
        body = (await response.json()) as InteractionIngestResponse;
      } catch {
        finish();
        settleBatch(items, classifyDelivery({ status: response.status }), gen);
        return;
      }
      finish();
      if (gen !== generation) return;
      if (response.ok && body && typeof body === 'object') {
        applyWatermarks(body);
        settleBatch(items, { transient: false, permanent: false }, gen);
        return;
      }
      settleBatch(items, classifyDelivery({ status: response.status }), gen);
    }).catch((error: unknown) => {
      finish();
      settleBatch(items, classifyDelivery({ error }), gen);
    });
  };

  const flushInternal = (force: boolean): void => {
    untrackTimer(debounceTimer);
    debounceTimer = null;
    untrackTimer(retryTimer);
    retryTimer = null;
    if (queue.length === 0) return;

    const nowTs = now();
    const pending: QueuedEvent[] = [];
    const kept: QueuedEvent[] = [];
    for (const item of queue) {
      if (force || item.readyAt <= nowTs) pending.push(item);
      else kept.push(item);
    }
    queue = kept;
    if (pending.length === 0) {
      if (queue.length > 0) scheduleRetry();
      return;
    }

    const groups = new Map<string, { sessionId: string; token: string | null; items: QueuedEvent[] }>();
    for (const item of pending) {
      if (!item.sessionId) {
        logEvent('permanent drop', { reason: 'no_session', count: 1 });
        continue;
      }
      const key = `${item.sessionId}::${item.token ?? ''}`;
      const group = groups.get(key);
      if (group) group.items.push(item);
      else groups.set(key, { sessionId: item.sessionId, token: item.token, items: [item] });
    }
    for (const group of groups.values()) {
      for (let i = 0; i < group.items.length; i += MAX_EVENTS_PER_BATCH) {
        postBatch(group.sessionId, group.token, group.items.slice(i, i + MAX_EVENTS_PER_BATCH));
      }
    }
    if (queue.length > 0) scheduleRetry();
  };

  const flushKeepalive = (): void => {
    // pagehide best-effort：keepalive 单请求 ≤64KB —— 批 ≤50 事件（≈15KB）
    // 安全；超出的部分放弃（遥测语义，诚实损失）。
    const bySession = new Map<string, QueuedEvent[]>();
    for (const item of queue) {
      if (!item.sessionId) continue;
      const list = bySession.get(item.sessionId) ?? [];
      if (list.length < MAX_EVENTS_PER_BATCH) list.push(item);
      bySession.set(item.sessionId, list);
    }
    queue = [];
    for (const [sessionId, items] of bySession) {
      postBatch(sessionId, items[0]?.token ?? null, items, true);
    }
  };

  const flush = (): void => {
    flushInternal(true);
  };

  const dispose = (): void => {
    generation += 1;
    untrackTimer(debounceTimer);
    debounceTimer = null;
    untrackTimer(retryTimer);
    retryTimer = null;
    queue = [];
  };

  return {
    enqueue,
    flush,
    flushKeepalive,
    dispose,
    acknowledgedSeq: () => ackedWatermark,
  };
}

/** pagehide 冲刷注册（bus 挂载时调用；返回反注册）。 */
export function registerPageHideFlush(flushKeepalive: () => void): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const handler = (): void => flushKeepalive();
  window.addEventListener('pagehide', handler);
  return () => window.removeEventListener('pagehide', handler);
}
