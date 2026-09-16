/**
 * ref-service — 数据面共享单例：调度器 + apiFetch 适配器 + 预算（v2）。
 *
 * 此前 5 个拉取点各自直接 apiFetch（各持一份临时去重/无预算/无取消语义）。
 * 本模块是它们共同的底座：
 *
 * - 并发 3、优先级出队、单飞去重、ETag 条件请求（304 廉价再验证）；
 * - 字节预算缓存（默认 256MB，与服务端 SpatialIndexCache 同档），可见层
 *   pin 逐出豁免；逐出事件入观测账本；
 * - 会话/单 ref 取消；取消后的迟到完成绝不写 store（调用方守卫之外
 *   调度器层还有一道硬闸）。
 *
 * 大层（>5000 要素 MVT-capable）不经此通道 —— 它们的显示走 MVT 瓦片，
 * 服务端已按 z/x/y 视口渐进 + ETag（本方向不重写 MVT）。
 */

import { apiFetch } from '@/lib/api/transport';
import { DataPlaneScheduler } from '@/lib/data-plane/scheduler';
import type { RefFetchRequest, RefFetchResult, RefFetchImpl } from '@/lib/data-plane/scheduler';
import { RefDataCache } from '@/lib/data-plane/cache';
import { recordDataPlaneEvent } from '@/lib/data-plane/observability';
import type { FeatureCollectionLike } from '@/lib/mapspec-runtime/source-diff';

/** 与服务端 SpatialIndexCache 的 max_bytes 同档（256MB）。 */
export const DEFAULT_DATA_PLANE_BUDGET_BYTES = 256 * 1024 * 1024;
export const DEFAULT_DATA_PLANE_CONCURRENCY = 3;
const REF_FETCH_TIMEOUT_MS = 120_000;

export interface DataPlaneConfig {
  budgetBytes?: number;
  concurrency?: number;
}

let scheduler: DataPlaneScheduler | null = null;
let config: DataPlaneConfig = {};

/** 测试/引导注入（可选）。必须在首个 request 前调用才生效。 */
export function configureDataPlane(next: DataPlaneConfig): void {
  config = { ...next };
  scheduler = null; // 重建单例（测试隔离）
}

/** 测试卸载。 */
export function _resetDataPlaneForTests(): void {
  scheduler = null;
  config = {};
}

export function getRefScheduler(): DataPlaneScheduler {
  if (!scheduler) {
    scheduler = new DataPlaneScheduler({
      concurrency: config.concurrency ?? DEFAULT_DATA_PLANE_CONCURRENCY,
      cache: new RefDataCache({
        maxBytes: config.budgetBytes ?? DEFAULT_DATA_PLANE_BUDGET_BYTES,
        onEvict: (key) => {
          const idx = key.indexOf('::');
          recordDataPlaneEvent('evict', {
            sessionId: idx >= 0 ? key.slice(0, idx) : undefined,
            refId: idx >= 0 ? key.slice(idx + 2) : key,
            reason: 'budget',
          });
        },
      }),
      fetchImpl: fetchRefViaApi,
    });
  }
  return scheduler;
}

/**
 * apiFetch 适配器：URL 拼装 / owner token / ETag 捕获 / 304 判别 /
 * 非.FeatureCollection 载荷的 fail-closed。
 */
export const fetchRefViaApi: RefFetchImpl = async (req, signal) => {
  const url = `/api/v1/layers/data/${encodeURIComponent(req.refId)}?session_id=${encodeURIComponent(req.sessionId)}`;
  let etag: string | undefined;
  try {
    const fc = await apiFetch<FeatureCollectionLike>(url, {
      signal,
      ownerToken: req.ownerToken ?? null,
      timeoutMs: REF_FETCH_TIMEOUT_MS,
      label: 'Data plane ref fetch error',
      onResponse: (response) => {
        const h = response.headers?.get?.('etag');
        if (h) etag = h;
      },
    });
    if (!fc || (fc.type !== 'FeatureCollection' && !Array.isArray((fc as { features?: unknown }).features))) {
      // 鸭子类型判状态码（与 store/layer-data 的 e.status 先例同款）：
      // 不 import 具体错误类，避免对 transport 的类型身份耦合。
      throw Object.assign(new Error('Data plane ref fetch error: non-FeatureCollection payload'), { status: 502 });
    }
    return { fc, etag };
  } catch (err) {
    if ((err as { status?: number } | null)?.status === 304) {
      return 'not-modified';
    }
    throw err;
  }
};

export interface RequestRefFCOptions {
  sessionId: string;
  refId: string;
  ownerToken?: string | null;
  urgency?: RefFetchRequest['urgency'];
  priority?: number;
  reasonCode?: string;
  signal?: AbortSignal;
  revalidate?: boolean;
}

/** 统一入口：ref → FeatureCollection（缓存/去重/预算/观测内建）。 */
export function requestRefFC(opts: RequestRefFCOptions): Promise<RefFetchResult> {
  recordDataPlaneEvent('request', {
    sessionId: opts.sessionId,
    refId: opts.refId,
    reason: opts.reasonCode,
  });
  return getRefScheduler().request({
    sessionId: opts.sessionId,
    refId: opts.refId,
    ownerToken: opts.ownerToken ?? null,
    urgency: opts.urgency,
    priority: opts.priority,
    reasonCode: opts.reasonCode,
    signal: opts.signal,
    revalidate: opts.revalidate,
  });
}

/** 会话切换：取消该会话全部在飞/排队 ref 拉取。 */
export function cancelRefSession(sessionId: string): number {
  return getRefScheduler().cancelSession(sessionId);
}
