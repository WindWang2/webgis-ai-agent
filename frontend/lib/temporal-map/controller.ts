/**
 * temporal-map 播放控制器（GOAL 03 M4/M7）。
 *
 * 纪律：
 * - **displayed_at 门控**：displayedAt 只在「帧过滤已应用且被观测」后更新
 *   （markApplied 回调由 map-panel 在 reconcile 落定后调用）—— UI 绝不
 *   先行宣称 2026 而画布还是 2023（Oracle 5）。
 * - **乱序拒绝**：帧应用带单调 seq；stale 回调（旧 reconcile 后到）被丢弃。
 * - **本地动画 + 停靠提交**：播放中的逐帧推进是本地 ephemeral（相机 P-9
 *   同款语义），不逐帧打 CAS；pause/seek/卸载时把落定游标经用户面 CAS
 *   提交（spec.temporal.cursor_frame = settled position）。
 * - **有界取数缓存**（M7）：fingerprint 键 LRU；重复拖动同帧零新请求。
 */
import type { MapSpecTemporalConfig, TemporalFrame } from '@/lib/mapspec-compiler/types';
import { temporalFingerprint } from './filter';

export interface TemporalMetrics {
  framesRequested: number;
  framesApplied: number;
  staleRejected: number;
  fetches: number;
  cacheHits: number;
}

export const temporalMetrics: TemporalMetrics = {
  framesRequested: 0,
  framesApplied: 0,
  staleRejected: 0,
  fetches: 0,
  cacheHits: 0,
};

/** 测试面：重置指标。 */
export function resetTemporalMetricsForTests(): void {
  temporalMetrics.framesRequested = 0;
  temporalMetrics.framesApplied = 0;
  temporalMetrics.staleRejected = 0;
  temporalMetrics.fetches = 0;
  temporalMetrics.cacheHits = 0;
}

export interface FrameRequest {
  seq: number;
  frame: TemporalFrame;
}

export type AppliedListener = (frame: TemporalFrame, seq: number) => void;

const BASE_INTERVAL_MS = 1000;
const FRAME_CACHE_LIMIT = 24;

/**
 * 帧取数 LRU（fingerprint 键）。播放/拖动的统计与表格联动取数经此缓存：
 * 同窗（同 ref+revision+field+frame+representation）命中 → 零网络。
 */
export class FrameFetchCache<V> {
  private map = new Map<string, { value: V; inFlight?: Promise<V> }>();

  get size(): number {
    return this.map.size;
  }

  peek(key: string): V | undefined {
    const hit = this.map.get(key);
    if (hit === undefined) return undefined;
    // LRU touch
    this.map.delete(key);
    this.map.set(key, hit);
    return hit.value;
  }

  async fetch(key: string, loader: () => Promise<V>): Promise<V> {
    const hit = this.map.get(key);
    if (hit !== undefined) {
      this.map.delete(key);
      this.map.set(key, hit);
      if (hit.value !== undefined) {
        temporalMetrics.cacheHits += 1;
        return hit.value;
      }
      if (hit.inFlight) {
        temporalMetrics.cacheHits += 1;
        return hit.inFlight;
      }
    }
    temporalMetrics.fetches += 1;
    const inFlight = loader().then((value) => {
      const slot = this.map.get(key);
      if (slot) slot.value = value;
      return value;
    });
    this.map.set(key, { value: undefined as V, inFlight });
    this.evict();
    return inFlight;
  }

  invalidateAll(): void {
    this.map.clear();
  }

  private evict(): void {
    while (this.map.size > FRAME_CACHE_LIMIT) {
      const oldest = this.map.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      this.map.delete(oldest);
    }
  }
}

/**
 * 播放控制器：一个会话一个实例（组件挂载创建；卸载 destroy 取消计时器）。
 */
export class TemporalPlaybackController {
  private seq = 0;
  private lastAppliedSeq = -1;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private destroyed = false;
  /** 当前已请求（未必然已应用）帧 —— stale 应用的判定基线。 */
  private pending: FrameRequest | null = null;

  readonly cache = new FrameFetchCache<unknown>();
  onApplied: AppliedListener | null = null;

  /** 请求切帧：返回 seq 令牌；应用方完成应用+观测后回 markApplied(seq)。 */
  requestFrame(frame: TemporalFrame): FrameRequest {
    this.seq += 1;
    temporalMetrics.framesRequested += 1;
    this.pending = { seq: this.seq, frame };
    return this.pending;
  }

  /**
   * 应用完成回调（reconcile 落定 + applied spec 观测之后）。
   * 乱序（seq ≤ lastAppliedSeq，或非当前 pending）→ 拒绝并计数。
   */
  markApplied(seq: number, frame: TemporalFrame): boolean {
    if (this.destroyed) return false;
    if (seq <= this.lastAppliedSeq || (this.pending && seq < this.pending.seq)) {
      temporalMetrics.staleRejected += 1;
      return false;
    }
    this.lastAppliedSeq = seq;
    temporalMetrics.framesApplied += 1;
    this.onApplied?.(frame, seq);
    return true;
  }

  /** 播放节拍：interval = BASE / rate（rate ∈ [0.25, 8]）。 */
  intervalFor(rate: number): number {
    const r = Math.min(8, Math.max(0.25, rate || 1));
    return Math.max(80, Math.round(BASE_INTERVAL_MS / r));
  }

  /** 启动本地动画（advance 每 tick 回调下一帧；末帧停播）。 */
  start(
    config: MapSpecTemporalConfig,
    advance: (frame: TemporalFrame) => void,
    onEnd?: () => void,
  ): void {
    this.stop();
    if (this.destroyed) return;
    const frames = config.frames || [];
    let idx = frames.findIndex((f) => f.id === config.cursor_frame);
    if (idx < 0) idx = 0;
    const tick = (): void => {
      if (this.destroyed) return;
      idx += 1;
      if (idx >= frames.length) {
        this.stop();
        onEnd?.();
        return;
      }
      advance(frames[idx]);
      this.timer = setTimeout(tick, this.intervalFor(config.playback?.rate ?? 1));
    };
    this.timer = setTimeout(tick, this.intervalFor(config.playback?.rate ?? 1));
  }

  stop(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  destroy(): void {
    this.stop();
    this.destroyed = true;
    this.cache.invalidateAll();
  }

  fingerprintFor(config: MapSpecTemporalConfig, frameId: string): string {
    return temporalFingerprint(config, frameId);
  }
}

/**
 * 会话切换/路由离开的全局清理钩子（M7 取消面）：map-panel 卸载或
 * session 变更时调用 —— 停播 + 清缓存（stale 会话帧绝不清缓存复用）。
 */
export function destroyControllerForSession(
  controllers: Map<string, TemporalPlaybackController>,
  sessionId: string,
): void {
  const c = controllers.get(sessionId);
  if (c) {
    c.destroy();
    controllers.delete(sessionId);
  }
}
