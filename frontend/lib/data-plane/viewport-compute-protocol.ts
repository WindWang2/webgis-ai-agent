/**
 * viewport-compute-protocol — 视口计算 worker 通道的**纯协议面**
 * （extreme-scale v2 / #1597 构建挂起修复）。
 *
 * 本模块只含纯数据类型与纯计算（worker 入口与主线程调度器共用同一实现，
 * 决策无分叉），**绝不** import 调度器（async-viewport-compute）或以
 * ``new URL`` 引用 worker 入口 —— 否则形成
 * ``async-viewport-compute ⇄ viewport.worker`` 的跨 worker 图循环，
 * Turbopack 生产构建会因此无限重算不收敛（CI 20 分钟超时，见 #1597；
 * 对照 mapspec-compiler/reconciler.worker：worker 入口同样只 import 纯模块）。
 */

import { filterFeaturesByBounds, thinFeaturesForViewport } from '@/lib/utils/geo';
import type { FeatureCollectionLike } from '@/lib/mapspec-runtime/source-diff';
import type { ViewportBBox } from '@/lib/data-plane/plan';

/** utils/geo 的结构子集类型（未导出）—— 与 source-diff 的 FC 在运行期
 *  同一形状；仅在调用边界做一次显式双向投影，绝不改变载荷。 */
type GeoFC = Parameters<typeof filterFeaturesByBounds>[0];

/** worker 通道的规模下界：低于它主线程计算 <1ms，通道成本不划算。 */
export const VIEWPORT_WORKER_MIN_FEATURES = 20_000;

// ─── 协议（主线程与 worker 两侧共用，纯数据） ────────────────────────────

export interface ViewportComputeInit {
  type: 'init-raw';
  token: number;
  raw: FeatureCollectionLike;
}

export interface ViewportComputeJob {
  type: 'filter-thin';
  token: number;
  viewport: ViewportBBox;
  budget: number;
  /** 关联标识：handler **必须**在结果里原样回传 —— 主线程靠它配对
   * pending（review P1 教训：协议字段缺失会让结果被静默丢弃）。 */
  jobId: number;
}

export type ViewportComputeRequest = ViewportComputeInit | ViewportComputeJob;

export interface ViewportComputeResult {
  type: 'filter-thin-result';
  jobId: number;
  token: number;
  viewport: ViewportBBox;
  budget: number;
  data: FeatureCollectionLike | null;
  /** 'unknown-token' = worker 端 raw 已被 FIFO 逐出（主线程应 re-init 重试）。 */
  error?: string;
}

/** 纯计算（worker 与同步回退共用同一实现 —— 决策无分叉）。 */
export function computeFilterThin(
  raw: FeatureCollectionLike,
  viewport: ViewportBBox,
  budget: number,
): FeatureCollectionLike {
  return thinFeaturesForViewport(
    filterFeaturesByBounds(raw as unknown as GeoFC, viewport),
    viewport,
    budget,
  ) as unknown as FeatureCollectionLike;
}

/** worker 入口本体（reconciler.worker 同款自守卫：仅 worker 上下文注册）。 */
export function handleViewportComputeRequest(
  req: ViewportComputeRequest,
  store: Map<number, FeatureCollectionLike>,
  post: (res: ViewportComputeResult) => void,
  maxStoreEntries = 8,
): void {
  if (req.type === 'init-raw') {
    store.set(req.token, req.raw);
    while (store.size > maxStoreEntries) {
      const oldest = store.keys().next().value;
      if (oldest === undefined) break;
      store.delete(oldest);
    }
    return;
  }
  const raw = store.get(req.token);
  if (!raw) {
    post({
      type: 'filter-thin-result',
      jobId: req.jobId,
      token: req.token,
      viewport: req.viewport,
      budget: req.budget,
      data: null,
      error: 'unknown-token',
    });
    return;
  }
  try {
    post({
      type: 'filter-thin-result',
      jobId: req.jobId,
      token: req.token,
      viewport: req.viewport,
      budget: req.budget,
      data: computeFilterThin(raw, req.viewport, req.budget),
    });
  } catch (err) {
    post({
      type: 'filter-thin-result',
      jobId: req.jobId,
      token: req.token,
      viewport: req.viewport,
      budget: req.budget,
      data: null,
      error: err instanceof Error ? err.message : String(err),
    });
  }
}
