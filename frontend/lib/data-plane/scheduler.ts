/**
 * DataPlaneScheduler — 数据面 ref 拉取的统一调度器（extreme-scale v2）。
 *
 * 统一此前 5 个各自为政的拉取点（SSE add-layer / session restore /
 * MapSpec ref-source-resolver / layer-data 按需水合 / data-fabric）背后的
 * 公共语义：
 *
 * - **并发上限 + 优先级出队**：interactive > normal > idle；同优先级 FIFO。
 *   无队头阻塞（慢请求不占槽等待，fetch 是异步的）。队列必然排空 —— 无饥饿。
 * - **单飞去重**：同 (session, ref) 的并发请求共享一次网络往返。
 * - **ETag 条件请求**：缓存命中带 etag 时走 If-None-Match；304 → 缓存继续
 *   服务（网络预算：重复挂载/会话重放不再整包重拉）。
 * - **stale-write 硬防护**：取消（会话切换/单 ref 失效/外部 signal）后的
 *   迟到完成**绝不**触发 onFulfilled —— 调度器层面的保证，各接线点自己的
 *   守卫（如 _refId 校验）是纵深防御的第二层。
 * - **失败不缓存不重试**（重试语义归 transport 的幂等重试）。
 */

import { RefDataCache } from '@/lib/data-plane/cache';
import type { FeatureCollectionLike } from '@/lib/mapspec-runtime/source-diff';

export type Urgency = 'interactive' | 'normal' | 'idle';

export interface RefFetchRequest {
  sessionId: string;
  refId: string;
  /**
   * 数据身份 revision（#1112 同 ref 覆盖语义：同 refId 可被新版本覆盖，
   * `content_revision` 是唯一区分）。进缓存键 —— 旧 revision 载荷绝不
   * 服务新请求。缺省 = 旧键格式（`sessionId::refId`，向后兼容）。
   */
  dataRevision?: number | string;
  ownerToken?: string | null;
  /** 数值越大越先出队；缺省由 urgency 投影（400/200/0）。 */
  priority?: number;
  urgency?: Urgency;
  reasonCode?: string;
  /** true = 越过新鲜窗强制条件验证（默认 false —— 新鲜窗 TTL 内直接服务）。 */
  revalidate?: boolean;
  /** 条件请求携带的实体标签（调度器从缓存注入，调用方无需手填）。 */
  etag?: string;
  /** 外部取消信号（会话切换/组件卸载）。与调度器自有的 controller 合流。 */
  signal?: AbortSignal;
}

/**
 * 数据面缓存键（F13/ADR-0214 D4 单一构造点）：
 *   无 revision → `sessionId::refId`（既有格式，向后兼容）
 *   有 revision → `sessionId::refId@rev`
 * 键格式知识只住在本函数 —— pin / 优先级 / 取消全部经它派生。
 */
export function refCacheKey(
  sessionId: string,
  refId: string,
  dataRevision?: number | string,
): string {
  const sid = sessionId || '';
  const rid = refId || '';
  if (dataRevision === undefined || dataRevision === null || dataRevision === '') {
    return `${sid}::${rid}`;
  }
  return `${sid}::${rid}@${String(dataRevision)}`;
}

/** 从缓存键提取 (sessionId, refId)（refId 剥 `@rev` 后缀；非键格式原样）。 */
export function parseRefCacheKey(key: string): { sessionId: string; refId: string } {
  const sep = key.indexOf('::');
  if (sep < 0) return { sessionId: '', refId: key };
  const refPart = key.slice(sep + 2);
  const at = refPart.lastIndexOf('@');
  return {
    sessionId: key.slice(0, sep),
    refId: at >= 0 ? refPart.slice(0, at) : refPart,
  };
}

export type RefFetchOutcome = { fc: FeatureCollectionLike; etag?: string } | 'not-modified';

export type RefFetchImpl = (
  req: RefFetchRequest,
  signal: AbortSignal,
) => Promise<RefFetchOutcome>;

export interface RefFetchResult {
  status: 'fulfilled' | 'not-modified' | 'cancelled' | 'failed';
  fc?: FeatureCollectionLike;
  etag?: string;
  error?: unknown;
  fromCache?: boolean;
}

export interface SchedulerCounters {
  enqueued: number;
  deduped: number;
  cacheHits: number;
  etag304: number;
  fetchOk: number;
  fetchFailed: number;
  cancelled: number;
  peakInflight: number;
}

export interface RefFetchDeps {
  fetchImpl: RefFetchImpl;
  cache?: RefDataCache;
  concurrency?: number;
  /** 缓存新鲜窗：窗口内的条目直接服务、不发条件请求（默认 30s）。
   *  revalidate:true 可越过窗口强制条件验证。 */
  freshTtlMs?: number;
  now?: () => number;
  /** 结算事件桥（观测账本）：fulfilled/not-modified/failed/cancelled/dedup
   *  全部入账 —— 计数器不许在生产恒零（证据诚实纪律）。 */
  onEvent?: (kind: 'fulfilled' | 'not-modified' | 'failed' | 'cancelled' | 'deduped' | 'cache-hit', detail?: { refId?: string; sessionId?: string; reason?: string }) => void;
}

export const DEFAULT_CONCURRENCY = 3;
export const DEFAULT_FRESH_TTL_MS = 30_000;

/** 原生 AbortError 判别（DOMException 在部分测试环境不可用，用名字契约）。 */
function isAbortError(err: unknown): boolean {
  return !!err
    && typeof err === 'object'
    && (err as { name?: string }).name === 'AbortError';
}

const URGENCY_PRIORITY: Record<Urgency, number> = {
  interactive: 400,
  normal: 200,
  idle: 0,
};

interface QueueItem {
  key: string;
  req: RefFetchRequest;
  priority: number;
  seq: number;
  controller: AbortController;
  cancelled: boolean;
  externalAbortHandler?: () => void;
}

interface WaiterGroup {
  item: QueueItem;
  waiters: Array<(r: RefFetchResult) => void>;
}

export class DataPlaneScheduler {
  private readonly fetchImpl: RefFetchImpl;
  private readonly cache: RefDataCache;
  private readonly concurrency: number;
  private readonly freshTtlMs: number;
  private readonly now: () => number;
  private readonly onEvent?: RefFetchDeps['onEvent'];
  private readonly queue: QueueItem[] = [];
  /** 出队后、结算前的等待者集合（dedup 的挂靠点）。 */
  private readonly pending = new Map<string, WaiterGroup>();
  private readonly inflight = new Set<string>();
  private readonly counters: SchedulerCounters = {
    enqueued: 0,
    deduped: 0,
    cacheHits: 0,
    etag304: 0,
    fetchOk: 0,
    fetchFailed: 0,
    cancelled: 0,
    peakInflight: 0,
  };
  private seq = 0;
  private onFulfilled?: (refId: string, fc: FeatureCollectionLike, etag?: string) => void;
  private idleWaiters: Array<() => void> = [];

  constructor(deps: RefFetchDeps) {
    this.fetchImpl = deps.fetchImpl;
    this.concurrency = Math.max(1, Math.floor(deps.concurrency ?? DEFAULT_CONCURRENCY));
    this.freshTtlMs = Math.max(0, deps.freshTtlMs ?? DEFAULT_FRESH_TTL_MS);
    this.now = deps.now ?? (() => Date.now());
    // 默认缓存必须共享调度器时钟（测试注入假 now 时，fetchedAt 与新鲜窗
    // 判定必须在同一时间轴上 —— 否则新鲜判定永远为真，stale 语义失效）。
    this.cache = deps.cache ?? new RefDataCache({ maxBytes: 256 * 1024 * 1024, now: this.now });
    this.onEvent = deps.onEvent;
  }

  setOnFulfilled(fn: (refId: string, fc: FeatureCollectionLike, etag?: string) => void): void {
    this.onFulfilled = fn;
  }

  stats(): SchedulerCounters {
    return { ...this.counters };
  }

  pendingCount(): number {
    return this.queue.length + this.inflight.size;
  }

  cachePeek(key: string): ReturnType<RefDataCache['peek']> {
    return this.cache.peek(key);
  }

  pinRef(key: string, pinned: boolean): boolean {
    return this.cache.setPinned(key, pinned);
  }

  /** 供接线点把可见层 pin 住（预算逐出永不触碰可见显示数据）。
   *  F13：经 visibility-pin 消费（useRefVisibilityPins → setRefPinned）。 */
  getCache(): RefDataCache {
    return this.cache;
  }

  /** 全部排空（队列空 + 无在飞）时 resolve —— 测试与优雅关停用。 */
  whenIdle(): Promise<void> {
    if (this.queue.length === 0 && this.inflight.size === 0) return Promise.resolve();
    return new Promise((resolve) => this.idleWaiters.push(resolve));
  }

  request(req: RefFetchRequest): Promise<RefFetchResult> {
    const key = refCacheKey(req.sessionId, req.refId, req.dataRevision);
    const forceRevalidate = req.revalidate === true;
    const entry = this.cache.get(key);

    // 缓存命中：仅「新鲜窗内」（review P2：过期且无 etag 的条目必须重新
    // 全量拉取 —— 否则对不发 ETag 的部署数据无限 stale）。
    const fresh = entry ? entry.fetchedAt + this.freshTtlMs > this.now() : false;
    if (entry && fresh && !forceRevalidate) {
      // #1385 F03：缓存命中也必须尊重 AbortSignal，否则会话切换后仍
      // 把 A 的 FC 当成 fulfilled 写进 B。
      if (req.signal?.aborted) {
        this.counters.cancelled += 1;
        this.onEvent?.('cancelled', { refId: req.refId, sessionId: req.sessionId, reason: 'cache-hit-aborted' });
        return Promise.resolve({ status: 'cancelled' });
      }
      this.counters.cacheHits += 1;
      this.onEvent?.('cache-hit', { refId: req.refId, sessionId: req.sessionId });
      this.safeFulfilled(req.refId, entry.fc, entry.etag);
      return Promise.resolve({
        status: 'fulfilled',
        fc: entry.fc,
        etag: entry.etag,
        fromCache: true,
      });
    }

    // 单飞去重：在飞**或排队中**的同 key 请求共享一次网络往返
    // （review P2：只查 inflight 会让队列饱和期的同 key 请求重复拉取）。
    const existingGroup = this.pending.get(key);
    if (existingGroup && (this.inflight.has(key) || this.queue.some((q) => q.key === key))) {
      this.counters.deduped += 1;
      this.onEvent?.('deduped', { refId: req.refId, sessionId: req.sessionId });
      return new Promise((resolve) => existingGroup.waiters.push(resolve));
    }

    const controller = new AbortController();
    const item: QueueItem = {
      key,
      req,
      priority: req.priority ?? URGENCY_PRIORITY[req.urgency ?? 'idle'],
      seq: ++this.seq,
      controller,
      cancelled: false,
    };
    if (req.signal) {
      const onAbort = () => this.cancelItem(item);
      if (req.signal.aborted) onAbort();
      else req.signal.addEventListener('abort', onAbort, { once: true });
      item.externalAbortHandler = onAbort;
    }
    if (item.cancelled) {
      return Promise.resolve({ status: 'cancelled' });
    }

    this.counters.enqueued += 1;
    return new Promise((resolve) => {
      const group = this.pending.get(key);
      if (group) {
        group.waiters.push(resolve);
      } else {
        this.pending.set(key, { item, waiters: [resolve] });
      }
      this.queue.push(item);
      this.pump();
    });
  }

  /** bump 排队中的请求优先级（viewport 变化 → 视口内层提级）。
   *  dataRevision 与 request 一致才能命中同一排队键（缺省匹配无 revision 键）。 */
  setPriority(
    refId: string,
    sessionId: string,
    priority: number,
    dataRevision?: number | string,
  ): boolean {
    const key = refCacheKey(sessionId, refId, dataRevision);
    const item = this.queue.find((q) => q.key === key);
    if (!item) return false;
    item.priority = priority;
    return true;
  }

  /**
   * 可见性 pin（F13/ADR-0214 D4 接线：预算逐出永不触碰可见显示数据）。
   * revision 无关：该 ref 的**全部**缓存代次同 pin/unpin —— 隐藏层
   * unpin 后由后续写入的自然逐出回收。返回受影响条目数（幂等）。
   */
  setRefPinned(sessionId: string, refId: string, pinned: boolean): number {
    let n = 0;
    for (const key of this.cache.keys()) {
      const parsed = parseRefCacheKey(key);
      if (parsed.sessionId === sessionId && parsed.refId === refId) {
        if (this.cache.setPinned(key, pinned)) n += 1;
      }
    }
    return n;
  }

  /** 会话级 unpin sweep（会话切换防 pinned 集合单调增长 —— pinned 是
   *  跨会话单例缓存上的状态，残留会把预算整体钉死）。返回触碰数。 */
  unpinSession(sessionId: string): number {
    let n = 0;
    for (const key of this.cache.keys()) {
      if (parseRefCacheKey(key).sessionId === sessionId) {
        if (this.cache.setPinned(key, false)) n += 1;
      }
    }
    return n;
  }

  /** 取消某会话全部请求（排队即弃；在飞 abort）。返回取消数（幂等去重）。 */
  cancelSession(sessionId: string): number {
    let n = 0;
    for (const item of [...this.queue, ...this.liveItems()]) {
      if (item.req.sessionId === sessionId && this.cancelItem(item)) {
        n += 1;
      }
    }
    return n;
  }

  cancelRef(refId: string, sessionId?: string): number {
    let n = 0;
    for (const item of [...this.queue, ...this.liveItems()]) {
      if (item.req.refId === refId && (!sessionId || item.req.sessionId === sessionId)) {
        if (this.cancelItem(item)) n += 1;
      }
    }
    return n;
  }

  /** 排队 + 在飞的全部存活 item（pending group 持有 item；去重靠 cancelItem 幂等）。 */
  private *liveItems(): Generator<QueueItem> {
    for (const group of this.pending.values()) {
      yield group.item;
    }
  }

  /** @returns 是否由本次调用真正取消（幂等：重复取消返回 false）。 */
  private cancelItem(item: QueueItem): boolean {
    if (item.cancelled) return false;
    item.cancelled = true;
    try {
      item.controller.abort();
    } catch {
      /* controller 已废弃 */
    }
    if (item.externalAbortHandler && item.req.signal) {
      item.req.signal.removeEventListener('abort', item.externalAbortHandler);
      item.externalAbortHandler = undefined;
    }
    const idx = this.queue.indexOf(item);
    if (idx >= 0) {
      this.queue.splice(idx, 1);
      this.counters.cancelled += 1;
      this.settle(item.key, { status: 'cancelled' });
      this.pump();
      return true;
    }
    // 在飞条目：不在这里 settle —— 等 fetch 落地后由 outcome 路径按
    // cancelled 标记结算（迟到完成绝不触发 onFulfilled）。
    return true;
  }

  private pump(): void {
    while (this.inflight.size < this.concurrency && this.queue.length > 0) {
      // 最高优先级；同优先级取最早入队（seq 最小）。
      let best = 0;
      for (let i = 1; i < this.queue.length; i += 1) {
        const q = this.queue[i];
        const b = this.queue[best];
        if (q.priority > b.priority || (q.priority === b.priority && q.seq < b.seq)) {
          best = i;
        }
      }
      const item = this.queue.splice(best, 1)[0];
      this.inflight.add(item.key);
      this.counters.peakInflight = Math.max(this.counters.peakInflight, this.inflight.size);
      this.startFetch(item);
    }
    this.notifyIdleIfDrained();
  }

  private notifyIdleIfDrained(): void {
    if (this.queue.length === 0 && this.inflight.size === 0) {
      const waiters = this.idleWaiters;
      this.idleWaiters = [];
      waiters.forEach((w) => w());
    }
  }

  private startFetch(item: QueueItem): void {
    const { key, req } = item;

    const entry = this.cache.get(key);
    const needsRevalidation = !!entry
      && !!entry.etag
      && (req.revalidate === true || entry.fetchedAt + this.freshTtlMs <= this.now());
    const effectiveReq: RefFetchRequest = needsRevalidation
      ? { ...req, etag: entry.etag }
      : req;

    // RefFetchImpl 的契约不保证 async —— 同步抛错必须走与异步失败同一条
    // 结算路径（review P2：否则 inflight/pending 永久楔死、并发槽泄漏）。
    let attempt: Promise<RefFetchOutcome>;
    try {
      attempt = Promise.resolve(this.fetchImpl(effectiveReq, item.controller.signal));
    } catch (err) {
      attempt = Promise.reject(err);
    }

    void attempt.then(
      (outcome) => {
        this.inflight.delete(key);
        if (item.cancelled) {
          this.counters.cancelled += 1;
          this.settle(key, { status: 'cancelled' });
        } else if (outcome === 'not-modified') {
          const cached = this.cache.get(key);
          if (!cached) {
            // 304 但缓存已被逐出 —— 诚实失败，由调用方决定重拉。
            this.counters.fetchFailed += 1;
            this.onEvent?.('failed', { refId: req.refId, sessionId: req.sessionId, reason: '304-but-evicted' });
            this.settle(key, { status: 'failed', error: new Error('304 but cache entry evicted') });
          } else {
            this.counters.etag304 += 1;
            this.onEvent?.('not-modified', { refId: req.refId, sessionId: req.sessionId });
            this.safeFulfilled(req.refId, cached.fc, cached.etag);
            this.settle(key, {
              status: 'not-modified',
              fc: cached.fc,
              etag: cached.etag,
              fromCache: true,
            });
          }
        } else {
          this.cache.set(key, outcome.fc, { etag: outcome.etag });
          this.counters.fetchOk += 1;
          this.onEvent?.('fulfilled', { refId: req.refId, sessionId: req.sessionId });
          this.safeFulfilled(req.refId, outcome.fc, outcome.etag);
          this.settle(key, { status: 'fulfilled', fc: outcome.fc, etag: outcome.etag });
        }
        this.pump();
      },
      (err) => {
        this.inflight.delete(key);
        // transport 契约：调用方主动 abort 以原生 AbortError 直通 —— 那是
        // 预期控制流（会话切换/组件卸载），不是失败：与显式取消同账结算，
        // 绝不进失败账（不触发墓碑/告警）。
        if (item.cancelled || isAbortError(err)) {
          this.counters.cancelled += 1;
          this.onEvent?.('cancelled', { refId: req.refId, sessionId: req.sessionId });
          this.settle(key, { status: 'cancelled' });
        } else {
          this.counters.fetchFailed += 1;
          this.onEvent?.('failed', { refId: req.refId, sessionId: req.sessionId, reason: 'fetch-error' });
          this.settle(key, { status: 'failed', error: err });
        }
        this.pump();
      },
    );
  }

  private settle(key: string, result: RefFetchResult): void {
    const group = this.pending.get(key);
    this.pending.delete(key);
    this.inflight.delete(key);
    group?.waiters.forEach((w) => w(result));
    this.notifyIdleIfDrained();
  }

  private safeFulfilled(refId: string, fc: FeatureCollectionLike, etag?: string): void {
    try {
      this.onFulfilled?.(refId, fc, etag);
    } catch {
      // 应用方失败不回滚网络结果；调度器的账（缓存/计数）保持真实。
    }
  }
}
