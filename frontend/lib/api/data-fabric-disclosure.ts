/**
 * data-fabric 披露留存（ADR-0142 D5 —— 断路器/缓存面板的数据通道）。
 *
 * 事实源边界（master @8b5b8375）：`engine_breaker.disclosure()`
 * （state/consecutive_failures/failure_threshold/cool_down_s/total_fallbacks）
 * 与 `result_cache` 披露（basis=ttl+fingerprint[+distributed]）只内嵌在
 * federation 查询响应载荷里，**没有独立轮询端点**；`stats()` 无 HTTP 面。
 *
 * 因此面板是「披露留存型」：前端任何拿到 data-fabric 联邦查询响应的代码，
 * 把载荷交给 recordFabricDisclosure()，这里抽出披露键、打上客户端接收时间、
 * 存入有界 LRU。面板读最近一次披露；从未收到 → 诚实空态（协调点：
 * 请求后端补独立只读端点，落地后本模块增加轮询通道）。
 */

export type BreakerState = 'closed' | 'open' | 'half_open';

export interface BreakerDisclosure {
  state: BreakerState;
  consecutive_failures: number;
  failure_threshold: number;
  cool_down_s: number;
  total_fallbacks: number;
  /** 客户端收到披露的时间（ISO）——不是后端产生披露的时间，面板须注明。 */
  observed_at: string;
}

export interface CacheDisclosure {
  hit: boolean;
  age_s: number | null;
  ttl_s: number | null;
  basis: string;
  key: string;
  observed_at: string;
}

export interface FabricDisclosureSnapshot {
  breaker: BreakerDisclosure | null;
  cache: CacheDisclosure | null;
  /** 留存条数（有界）——0 表示从未收到任何披露。 */
  recorded: number;
}

const MAX_HISTORY = 32;

let breakerHistory: BreakerDisclosure[] = [];
let cacheHistory: CacheDisclosure[] = [];
const listeners = new Set<(snap: FabricDisclosureSnapshot) => void>();

function isBreakerState(v: unknown): v is BreakerState {
  return v === 'closed' || v === 'open' || v === 'half_open';
}

/** 从任意 data-fabric 响应载荷中抽取披露键（容忍缺键/形状漂移）。 */
export function recordFabricDisclosure(payload: unknown): boolean {
  if (!payload || typeof payload !== 'object') return false;
  const now = new Date().toISOString();
  let recorded = false;
  const breaker = (payload as Record<string, unknown>).engine_breaker;
  if (breaker && typeof breaker === 'object') {
    const b = breaker as Record<string, unknown>;
    if (isBreakerState(b.state)) {
      breakerHistory.unshift({
        state: b.state,
        consecutive_failures: typeof b.consecutive_failures === 'number' ? b.consecutive_failures : 0,
        failure_threshold: typeof b.failure_threshold === 'number' ? b.failure_threshold : 3,
        cool_down_s: typeof b.cool_down_s === 'number' ? b.cool_down_s : 60,
        total_fallbacks: typeof b.total_fallbacks === 'number' ? b.total_fallbacks : 0,
        observed_at: now,
      });
      breakerHistory = breakerHistory.slice(0, MAX_HISTORY);
      recorded = true;
    }
  }
  const cache = (payload as Record<string, unknown>).result_cache;
  if (cache && typeof cache === 'object') {
    const c = cache as Record<string, unknown>;
    if (typeof c.basis === 'string') {
      cacheHistory.unshift({
        hit: c.hit === true,
        age_s: typeof c.age_s === 'number' ? c.age_s : null,
        ttl_s: typeof c.ttl_s === 'number' ? c.ttl_s : null,
        basis: c.basis,
        key: typeof c.key === 'string' ? c.key : '',
        observed_at: now,
      });
      cacheHistory = cacheHistory.slice(0, MAX_HISTORY);
      recorded = true;
    }
  }
  if (recorded) {
    const snap = getDisclosureSnapshot();
    for (const fn of listeners) fn(snap);
  }
  return recorded;
}

export function getDisclosureSnapshot(): FabricDisclosureSnapshot {
  return {
    breaker: breakerHistory[0] ?? null,
    cache: cacheHistory[0] ?? null,
    recorded: breakerHistory.length + cacheHistory.length,
  };
}

export function subscribeFabricDisclosure(fn: (snap: FabricDisclosureSnapshot) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** 测试隔离用：清空留存并移除监听。 */
export function resetFabricDisclosureForTests(): void {
  breakerHistory = [];
  cacheHistory = [];
  listeners.clear();
}
