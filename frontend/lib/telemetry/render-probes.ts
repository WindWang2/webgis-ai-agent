/**
 * RenderPerfProbes — 渲染性能探针（F13，ADR-0214 D5）。
 *
 * 此前 TTFR / patch latency / data bytes / cache hit / render failure
 * 全仓零埋点：perf-counters 自述 test-only，data-plane observability 是
 * 本地账本无出口。本模块把探针聚合成 versioned perf 块，随 RenderObservation
 * 的 `perf` 键上行（服务端 render_perf_probes.v1 有界归一后落 trace）。
 *
 * 纪律：
 *
 * - **纯读 + 墙钟标记**：订阅 session-cursor 代次（desired 变化的唯一
 *   信号源，不造第二事件流）；其余全部直读既有账本（data-plane
 *   observability / 调度器缓存），不复制计数器；
 * - **有界**：块内只有数字/布尔/封闭键（≤2KB），绝不携带 GeoJSON /
 *   特征数据 / 自由文本；
 * - **诚实缺席**：无 desired 变化记录（如探针晚于首个 commit 启动）
 *   → ttfr/patch latency 缺席，绝不虚构 0；
 * - **会话隔离**：reset 随会话切换（由 observation hook 消费）。
 *
 * 语义：
 *
 *   patch_latency_ms = 最近一次 desired 变化 → 本次 render settle
 *                      （settle 晚于上次 settle 才有意义 —— 同一稳态
 *                      的重复采集不改写）
 *   ttfr_ms          = 会话首个 desired 变化 → 首次 mapIdle settle
 *                      （会话级一次，time-to-first-render）
 */

import { getDataPlaneSnapshot } from '@/lib/data-plane/observability';
import { getRefScheduler } from '@/lib/data-plane/ref-service';
import { subscribeMapSpecLive } from '@/lib/mapspec/session-cursor';

export const PERF_SCHEMA_VERSION = 'render_perf_probes.v1' as const;

export interface RenderPerfBlock {
  schema_version: typeof PERF_SCHEMA_VERSION;
  ttfr_ms?: number;
  patch_latency_ms?: number;
  map_idle: boolean;
  render_failures: number;
  data_plane: {
    requests: number;
    cacheHits: number;
    etag304: number;
    fetchOk: number;
    fetchFailed: number;
    cancelled: number;
    deduped: number;
    evictions: number;
  };
  cache_bytes: number;
}

let subscribed = false;
let firstDesiredAt: number | undefined;
let lastDesiredAt: number | undefined;
let lastSettledAt: number | undefined;
let ttfrMs: number | undefined;
let patchLatencyMs: number | undefined;

function ensureSubscribed(): void {
  if (subscribed) return;
  subscribed = true;
  try {
    subscribeMapSpecLive(() => {
      const now = Date.now();
      lastDesiredAt = now;
      if (firstDesiredAt === undefined) firstDesiredAt = now;
    });
  } catch {
    // 订阅失败只损失时延探针 —— 诚实缺席，绝不抛回观察链路。
  }
}

/** render settle 完成时打点（observation 采集路径调用；幂等安全）。 */
export function noteRenderSettled({ mapIdle }: { mapIdle: boolean }): void {
  ensureSubscribed();
  const now = Date.now();
  if (
    lastDesiredAt !== undefined
    && (lastSettledAt === undefined || lastDesiredAt > lastSettledAt)
  ) {
    patchLatencyMs = Math.max(0, now - lastDesiredAt);
  }
  lastSettledAt = now;
  if (ttfrMs === undefined && firstDesiredAt !== undefined && mapIdle) {
    ttfrMs = Math.max(0, now - firstDesiredAt);
  }
}

/** 当前 perf 块快照（随 observation 上行；纯数字/封闭键）。 */
export function snapshotRenderPerfBlock({
  renderFailures = 0,
  mapIdle = false,
}: {
  renderFailures?: number;
  mapIdle?: boolean;
}): RenderPerfBlock {
  ensureSubscribed();
  let dataPlane: RenderPerfBlock['data_plane'] = {
    requests: 0, cacheHits: 0, etag304: 0, fetchOk: 0,
    fetchFailed: 0, cancelled: 0, deduped: 0, evictions: 0,
  };
  try {
    const counters = getDataPlaneSnapshot().counters;
    dataPlane = {
      requests: counters.requests,
      cacheHits: counters.cacheHits,
      etag304: counters.etag304,
      fetchOk: counters.fetchOk,
      fetchFailed: counters.fetchFailed,
      cancelled: counters.cancelled,
      deduped: counters.deduped,
      evictions: counters.evictions,
    };
  } catch {
    // 账本缺席 → 全零计数（诚实：与「无请求」不可区分，但绝不虚构）
  }
  let cacheBytes = 0;
  try {
    // 惰性单例：只读 totalBytes；构造不触发网络（无 request 即无 fetch）。
    cacheBytes = Math.max(0, getRefScheduler().getCache().totalBytes);
  } catch {
    cacheBytes = 0;
  }
  const block: RenderPerfBlock = {
    schema_version: PERF_SCHEMA_VERSION,
    map_idle: mapIdle === true,
    render_failures: Math.max(0, Math.floor(renderFailures) || 0),
    data_plane: dataPlane,
    cache_bytes: cacheBytes,
  };
  if (ttfrMs !== undefined) block.ttfr_ms = ttfrMs;
  if (patchLatencyMs !== undefined) block.patch_latency_ms = patchLatencyMs;
  return block;
}

/** 会话切换清零（observation hook 的 sessionId effect 消费）。 */
export function resetRenderProbes(): void {
  firstDesiredAt = undefined;
  lastDesiredAt = undefined;
  lastSettledAt = undefined;
  ttfrMs = undefined;
  patchLatencyMs = undefined;
}

/** 测试卸载（含订阅 —— 模块级监听不得跨测试泄漏）。 */
export function _resetRenderProbesForTests(): void {
  resetRenderProbes();
  subscribed = false;
}
