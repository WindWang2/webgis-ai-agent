/**
 * 数据面统一观测（extreme-scale v2）—— 慢路径/304/ETag/单飞/取消/逐出
 * 的结构化账本。纪律与 utils/perf-counters 相同：
 *
 * - 纯整数计数 + 有界环形明细，无几何数据、无 PII、无 chain-of-thought；
 * - 生产挂载零分配压力（只有 record 时一个对象入环，环满覆盖）；
 * - 证据诚实：失败/取消/逐出与成功同权重入账，绝不静默吞掉。
 */

export type DataPlaneEventKind =
  | 'request'
  | 'dedup'
  | 'cache-hit'
  | 'etag-304'
  | 'fetch-ok'
  | 'fetch-failed'
  | 'cancelled'
  | 'evict'
  | 'patch-applied'
  | 'patch-fallback';

export interface DataPlaneEvent {
  at: number;
  kind: DataPlaneEventKind;
  refId?: string;
  sessionId?: string;
  /** 封闭词表理由（决策 reason / 失败类别），自由文本仅限 error message。 */
  reason?: string;
  durationMs?: number;
}

export interface DataPlaneCounterSnapshot {
  requests: number;
  deduped: number;
  cacheHits: number;
  etag304: number;
  fetchOk: number;
  fetchFailed: number;
  cancelled: number;
  evictions: number;
  patchApplied: number;
  patchFallbacks: number;
}

const RING_CAPACITY = 128;

const counters: DataPlaneCounterSnapshot = {
  requests: 0,
  deduped: 0,
  cacheHits: 0,
  etag304: 0,
  fetchOk: 0,
  fetchFailed: 0,
  cancelled: 0,
  evictions: 0,
  patchApplied: 0,
  patchFallbacks: 0,
};

const ring: DataPlaneEvent[] = [];
let ringAt = 0;

const COUNTER_FOR_KIND: Record<DataPlaneEventKind, keyof DataPlaneCounterSnapshot> = {
  request: 'requests',
  dedup: 'deduped',
  'cache-hit': 'cacheHits',
  'etag-304': 'etag304',
  'fetch-ok': 'fetchOk',
  'fetch-failed': 'fetchFailed',
  cancelled: 'cancelled',
  evict: 'evictions',
  'patch-applied': 'patchApplied',
  'patch-fallback': 'patchFallbacks',
};

export function recordDataPlaneEvent(
  kind: DataPlaneEventKind,
  detail: Omit<DataPlaneEvent, 'at' | 'kind'> = {},
): void {
  counters[COUNTER_FOR_KIND[kind]] += 1;
  const event: DataPlaneEvent = { at: Date.now(), kind, ...detail };
  if (ring.length < RING_CAPACITY) {
    ring.push(event);
  } else {
    ring[ringAt % RING_CAPACITY] = event;
  }
  ringAt += 1;
}

/** 只读快照（计数拷贝 + 环按时间序）。 */
export function getDataPlaneSnapshot(): {
  counters: DataPlaneCounterSnapshot;
  recent: DataPlaneEvent[];
} {
  const ordered =
    ring.length < RING_CAPACITY
      ? [...ring]
      : [...ring.slice(ringAt % RING_CAPACITY), ...ring.slice(0, ringAt % RING_CAPACITY)];
  return { counters: { ...counters }, recent: ordered };
}

export function resetDataPlaneObservabilityForTests(): void {
  for (const k of Object.keys(counters) as Array<keyof DataPlaneCounterSnapshot>) {
    counters[k] = 0;
  }
  ring.length = 0;
  ringAt = 0;
}
