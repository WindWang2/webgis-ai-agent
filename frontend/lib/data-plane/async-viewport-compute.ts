/**
 * async-viewport-compute — 大 inline FeatureCollection 的视口过滤+抽稀
 * off-main-thread 通道（extreme-scale v2 / milestone M6）。
 *
 * 现状：``renderer.refreshGeoJsonSourcesByViewport`` 对 raw ≥ 阈值的
 * source 在主线程跑 ``filterFeaturesByBounds + thinFeaturesForViewport``
 * （O(n) —— 100k 要素层数十 ms，idle 回调里仍是一次 jank）。本模块按
 * mapspec-compiler/worker-bridge 的成熟模式（worker + 透明同步回退 +
 * 身份 token 免重复 structured-clone）把重活搬进 Web Worker：
 *
 * - **raw 身份 token**（FE-02 同款）：同一 raw 引用只上传一次；worker 端
 *   按 token 持有 raw，后续视口刷新只发 {token, viewport, budget}；
 * - **同视口 memo**：与 renderer 的 _filteredBySource 缓存同语义 ——
 *   稳定视口零 roundtrip；
 * - **透明回退**：Worker 不可用（SSR/测试/打包限制）→ 主线程同步计算，
 *   调用方拿到的 Promise 形状完全一致；
 * - **有界清理**：worker 端 raw store FIFO 有界；dispose 时整体释放。
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
   *  pending（review P1 教训：协议字段缺失会让结果被静默丢弃）。 */
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

// ─── 主线程调度器 ────────────────────────────────────────────────────────

let sharedWorker: WorkerLike | null = null;
let nextToken = 1;
let nextJobId = 0;

const rawTokenByRef = new WeakMap<object, number>();
/** 同视口 memo：{rawRef → {viewport, budget, result}}（稳定视口零往返）。 */
const lastApplied = new WeakMap<
  object,
  { viewport: ViewportBBox; budget: number; result: FeatureCollectionLike }
>();

const pending = new Map<
  number,
  {
    resolve: (r: FeatureCollectionLike | null) => void;
    raw: FeatureCollectionLike;
    token: number;
    viewport: ViewportBBox;
    budget: number;
    timer?: ReturnType<typeof setTimeout>;
  }
>();

/** 单个 job 的硬超时：worker 楔死（脚本错误/OOM）时 resolve null，
 *  调用方回退同步路径 —— 绝不让视口刷新永久挂起（review P1 教训）。 */
const VIEWPORT_JOB_TIMEOUT_MS = 10_000;

interface WorkerLike {
  postMessage: (msg: unknown) => void;
  addEventListener: (t: 'message', cb: (e: MessageEvent<ViewportComputeResult>) => void) => void;
  terminate: () => void;
  onerror?: unknown;
}

function createWorker(): WorkerLike | null {
  if (sharedWorker) return sharedWorker;
  try {
    if (typeof Worker === 'undefined' || typeof URL === 'undefined') return null;
    // 由打包器按 URL 实例化（与 worker-bridge 同款；不在主线程 import 入口）。
    sharedWorker = new Worker(
      new URL('./viewport.worker', import.meta.url),
      { type: 'module' },
    ) as unknown as WorkerLike;
    return sharedWorker;
  } catch {
    // 环境无 worker 支持（SSR/测试/打包限制）→ 同步回退。
    return null;
  }
}

/** 测试/卸载：丢弃 worker 与 memo（raw 本体在调用方，无泄漏）。 */
export function resetViewportComputeForTests(): void {
  if (sharedWorker) {
    try {
      sharedWorker.terminate();
    } catch {
      /* already gone */
    }
  }
  sharedWorker = null;
  uploadedTokens.clear();
  for (const job of pending.values()) {
    if (job.timer) clearTimeout(job.timer);
    job.resolve(null);
  }
  pending.clear();
}

/** worker 死亡/结果不可用：清空全部 pending（resolve null → 调用方回退），
 *  并丢弃缓存句柄使下次调用重建。 */
function killWorkerAndFlush(): void {
  const dead = sharedWorker;
  if (dead) {
    try {
      dead.terminate();
    } catch {
      /* already gone */
    }
    if (sharedWorker === dead) sharedWorker = null;
  }
  for (const job of pending.values()) {
    if (job.timer) clearTimeout(job.timer);
    job.resolve(null);
  }
  pending.clear();
}

function settleJob(jobId: number, data: FeatureCollectionLike | null): void {
  const job = pending.get(jobId);
  if (!job) return;
  pending.delete(jobId);
  if (job.timer) clearTimeout(job.timer);
  if (data) {
    lastApplied.set(job.raw, {
      viewport: [...job.viewport] as ViewportBBox,
      budget: job.budget,
      result: data,
    });
  }
  job.resolve(data);
}

function ensureWorker(): WorkerLike | null {
  const worker = createWorker();
  if (!worker) return null;
  if (!(worker as unknown as { __wired?: boolean }).__wired) {
    (worker as unknown as { __wired?: boolean }).__wired = true;
    worker.addEventListener('message', (event) => {
      const res = event.data;
      // jobId 由协议保证回传（handler echo）；无 jobId 的消息一律丢弃。
      if (!res || res.type !== 'filter-thin-result' || typeof res.jobId !== 'number') return;
      if (!pending.has(res.jobId)) return;
      if (res.error === 'unknown-token') {
        // worker 端 raw store FIFO 逐出了该 token：重新上传 raw 再补发一次
        // 同 jobId 的 job（幂等：同一 pending 条目，settle 恰一次）。
        const job = pending.get(res.jobId)!;
        try {
          worker.postMessage({ type: 'init-raw', token: job.token, raw: job.raw } satisfies ViewportComputeInit);
          worker.postMessage({
            type: 'filter-thin',
            token: job.token,
            viewport: job.viewport,
            budget: job.budget,
            jobId: res.jobId,
          } satisfies ViewportComputeJob);
          return;
        } catch {
          settleJob(res.jobId, null);
          return;
        }
      }
      settleJob(res.jobId, res.data);
    });
    // worker 脚本错误/结构化克隆失败：整体降级（本批 resolve null）。
    (worker as { onerror?: unknown }).onerror = () => killWorkerAndFlush();
  }
  return worker;
}

/**
 * 大集合视口计算：worker 可用走 off-main-thread（raw 首次上传 + token 复
 * 用）；不可用走主线程同步计算。resolve null = 通道失败（调用方必须回退
 * 既有同步路径，语义安全 —— 与 master 行为等价）。
 */
export function computeFilterThinAsync(
  raw: FeatureCollectionLike,
  viewport: ViewportBBox,
  budget: number,
): Promise<FeatureCollectionLike | null> {
  const memo = lastApplied.get(raw);
  if (memo && memo.budget === budget && sameViewport(memo.viewport, viewport)) {
    return Promise.resolve(memo.result);
  }

  const worker = ensureWorker();
  if (!worker) {
    try {
      const result = computeFilterThin(raw, viewport, budget);
      lastApplied.set(raw, { viewport: [...viewport] as ViewportBBox, budget, result });
      return Promise.resolve(result);
    } catch {
      return Promise.resolve(null);
    }
  }

  let token = rawTokenByRef.get(raw);
  if (token === undefined) {
    const fresh = ++nextToken;
    rawTokenByRef.set(raw, fresh);
    token = fresh;
  }
  const rawToken: number = token;
  // token 可能已被 worker 端 FIFO 逐出而 WeakMap 侧仍持有 —— unknown-token
  // 的重传路径（message handler 内）覆盖该情况；首见 token 必然带 raw 上传。
  if (!workerHasToken(rawToken)) {
    // raw 只在需要（重）上传时 structured-clone 一次（成本摊销）。
    worker.postMessage({ type: 'init-raw', token: rawToken, raw } satisfies ViewportComputeInit);
    markTokenUploaded(rawToken);
  }

  const jobId = ++nextJobId;
  return new Promise((resolve) => {
    const timer = setTimeout(() => settleJob(jobId, null), VIEWPORT_JOB_TIMEOUT_MS);
    pending.set(jobId, { resolve, raw, token: rawToken, viewport, budget, timer });
    worker.postMessage({
      type: 'filter-thin',
      token: rawToken,
      viewport,
      budget,
      jobId,
    } satisfies ViewportComputeJob);
  });
}

// token 上传台账（module 级 Set）：WeakMap 无法回答「worker 端是否已有
// 该 raw」—— 显式记录已上传 token，避免每次刷新重复 structured-clone。
const uploadedTokens = new Set<number>();
function workerHasToken(token: number): boolean {
  return uploadedTokens.has(token);
}
function markTokenUploaded(token: number): void {
  uploadedTokens.add(token);
  while (uploadedTokens.size > 32) {
    const oldest = uploadedTokens.values().next().value;
    if (oldest === undefined) break;
    uploadedTokens.delete(oldest);
  }
}

function sameViewport(a: ViewportBBox, b: ViewportBBox): boolean {
  return a[0] === b[0] && a[1] === b[1] && a[2] === b[2] && a[3] === b[3];
}
