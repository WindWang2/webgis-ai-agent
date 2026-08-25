import type maplibregl from 'maplibre-gl';
import { devOnly } from '@/lib/utils/logger';

export type RenderOpType =
  | 'SET_PAINT'
  | 'SET_LAYOUT'
  | 'SET_FILTER'
  | 'UPDATE_GEOJSON'
  | 'ADD_LAYER'
  | 'REMOVE_LAYER'
  | 'SET_STYLE';

export interface RenderOperation {
  /** Key used for deduplication, e.g. "paint:custom-layer-fill:fill-opacity" */
  id: string;
  type: RenderOpType;
  priority?: 'high' | 'normal';
  execute: (map: maplibregl.Map) => void;
}

export interface DebouncerOptions {
  /** Maximum time in milliseconds allowed per rAF frame for executing render ops. Default: 10ms */
  frameBudgetMs?: number;
  /** Optional callback for telemetry / dev performance monitoring */
  onFrameStats?: (stats: FrameStats) => void;
}

export interface FrameStats {
  executedOps: number;
  remainingOps: number;
  durationMs: number;
  budgetExceeded: boolean;
}

export class RenderDebouncer {
  private map: maplibregl.Map | null;
  private highPriorityQueue: Map<string, RenderOperation> = new Map();
  private normalPriorityQueue: Map<string, RenderOperation> = new Map();
  private rafId: number | null = null;
  private frameBudgetMs: number;
  private onFrameStats?: (stats: FrameStats) => void;
  private isDisposed = false;

  constructor(map: maplibregl.Map | null, options: DebouncerOptions = {}) {
    this.map = map;
    this.frameBudgetMs = options.frameBudgetMs ?? 10;
    this.onFrameStats = options.onFrameStats;
  }

  /**
   * Enqueue a render operation. Coalesces with existing operation of same `id`.
   */
  public enqueue(op: RenderOperation): void {
    if (this.isDisposed) return;

    const queue = op.priority === 'high' ? this.highPriorityQueue : this.normalPriorityQueue;
    queue.set(op.id, op);

    this.scheduleFrame();
  }

  /**
   * Immediately flush all queued operations synchronously (e.g. before unmount or screenshot).
   */
  public flush(): void {
    if (this.rafId !== null) {
      if (typeof cancelAnimationFrame !== 'undefined') {
        cancelAnimationFrame(this.rafId);
      }
      this.rafId = null;
    }
    this.processQueue(Infinity);
  }

  public pendingCount(): number {
    return this.highPriorityQueue.size + this.normalPriorityQueue.size;
  }

  private scheduleFrame(): void {
    if (this.rafId === null && !this.isDisposed) {
      if (typeof requestAnimationFrame !== 'undefined') {
        this.rafId = requestAnimationFrame(() => {
          this.rafId = null;
          this.processQueue(this.frameBudgetMs);
        });
      } else {
        // Microtask fallback for Node / test environments
        this.rafId = setTimeout(() => {
          this.rafId = null;
          this.processQueue(this.frameBudgetMs);
        }, 0) as unknown as number;
      }
    }
  }

  private processQueue(budgetMs: number): void {
    if (this.isDisposed || !this.map) return;

    const startTime = typeof performance !== 'undefined' ? performance.now() : Date.now();
    let executedOps = 0;

    // Process high priority queue first
    for (const [id, op] of Array.from(this.highPriorityQueue.entries())) {
      this.highPriorityQueue.delete(id);
      try {
        op.execute(this.map);
      } catch (err) {
        // #1008：裸 console（NODE_ENV 手工门禁）统一换 devOnly。
        devOnly.warn(`[RenderDebouncer] Operation execution failed for ${id}:`, err);
      }
      executedOps++;

      const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
      if (now - startTime >= budgetMs) {
        break;
      }
    }

    // Process normal priority queue if budget permits
    const nowCheck = typeof performance !== 'undefined' ? performance.now() : Date.now();
    if (nowCheck - startTime < budgetMs) {
      for (const [id, op] of Array.from(this.normalPriorityQueue.entries())) {
        this.normalPriorityQueue.delete(id);
        try {
          op.execute(this.map);
        } catch (err) {
          // #1008：同上，统一 devOnly 门禁。
          devOnly.warn(`[RenderDebouncer] Operation execution failed for ${id}:`, err);
        }
        executedOps++;

        const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
        if (now - startTime >= budgetMs) {
          break;
        }
      }
    }

    const endTime = typeof performance !== 'undefined' ? performance.now() : Date.now();
    const durationMs = endTime - startTime;
    const remainingOps = this.highPriorityQueue.size + this.normalPriorityQueue.size;
    const budgetExceeded = remainingOps > 0;

    if (this.onFrameStats) {
      this.onFrameStats({ executedOps, remainingOps, durationMs, budgetExceeded });
    }

    // If operations remain, schedule next frame
    if (remainingOps > 0) {
      this.scheduleFrame();
    }
  }

  public dispose(): void {
    this.isDisposed = true;
    if (this.rafId !== null) {
      if (typeof cancelAnimationFrame !== 'undefined') {
        cancelAnimationFrame(this.rafId);
      }
      this.rafId = null;
    }
    this.highPriorityQueue.clear();
    this.normalPriorityQueue.clear();
  }
}
