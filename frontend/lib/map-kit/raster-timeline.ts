/**
 * Raster timeline — 时序切片懒加载基座（P7）。
 *
 * 长时序（≥48 步）播放的两条纪律：
 *   1. 切片不一次性全进内存：createTimelineLoader 按需取帧，LRU 有界缓存 +
 *      滑动窗口预取（ahead of playhead），in-flight 去重防抖动回放时的重复请求；
 *   2. 渲染不卡主线程：帧位图（gridToRgba 产物）同样进 LRU —— 已渲染帧的
 *      播放是纯 blit；未命中帧由 rAF 节流的渲染循环处理，一帧最多渲一张。
 *
 * 纯逻辑（缓存/预取/节流决策）与 DOM 隔离，性能断言（§5 门禁）直接测本模块。
 */

export type FrameGrid = number[][];

/** 有界 LRU（Map 迭代序 = 插入序；get 命中重新插入保持新鲜度）。 */
export class LruCache<K, V> {
  private readonly entries = new Map<K, V>();

  constructor(public readonly capacity: number) {
    if (capacity < 1) throw new Error('LruCache capacity must be >= 1');
  }

  has(key: K): boolean {
    return this.entries.has(key);
  }

  get(key: K): V | undefined {
    if (!this.entries.has(key)) return undefined;
    const value = this.entries.get(key) as V;
    this.entries.delete(key);
    this.entries.set(key, value);
    return value;
  }

  put(key: K, value: V): void {
    if (this.entries.has(key)) this.entries.delete(key);
    this.entries.set(key, value);
    while (this.entries.size > this.capacity) {
      const oldest = this.entries.keys().next().value as K;
      this.entries.delete(oldest);
    }
  }

  get size(): number {
    return this.entries.size;
  }

  clear(): void {
    this.entries.clear();
  }
}

export interface TimelineLoaderOptions {
  /** 总步数。 */
  total: number;
  /** 取帧函数（index → grid）；由调用方接 readCubeWindow / 内存数组。 */
  fetchSlice: (index: number) => Promise<FrameGrid>;
  /** 缓存帧数（默认 24 —— 48 步序列的一半，内存与命中率的折中）。 */
  cacheSize?: number;
  /** 播放方向上预取几帧（默认 4）。 */
  prefetchAhead?: number;
}

export interface TimelineLoader {
  /** 取帧（命中缓存同步 resolve；未命中取网并预取前方窗口）。 */
  load(index: number): Promise<FrameGrid>;
  /** 同步命中判定（渲染循环用它决定本帧是否零工作）。 */
  has(index: number): boolean;
  /** 预取排队数（测试/调试观测）。 */
  stats(): { cached: number; inFlight: number; fetches: number };
}

export function createTimelineLoader(opts: TimelineLoaderOptions): TimelineLoader {
  const cache = new LruCache<number, FrameGrid>(opts.cacheSize ?? 24);
  const inFlight = new Map<number, Promise<FrameGrid>>();
  let fetches = 0;
  let playhead = 0;

  function request(index: number): Promise<FrameGrid> {
    if (index < 0 || index >= opts.total) {
      return Promise.reject(new Error(`slice ${index} out of range [0, ${opts.total})`));
    }
    const cached = cache.get(index);
    if (cached) return Promise.resolve(cached);
    const pending = inFlight.get(index);
    if (pending) return pending;
    fetches += 1;
    const p = opts
      .fetchSlice(index)
      .then((grid) => {
        cache.put(index, grid);
        inFlight.delete(index);
        return grid;
      })
      .catch((e) => {
        inFlight.delete(index);
        throw e;
      });
    inFlight.set(index, p);
    return p;
  }

  return {
    load(index: number) {
      playhead = index;
      // 滑动窗口预取：playhead + 1 .. + ahead（不 await —— 不阻塞当前帧）。
      const ahead = opts.prefetchAhead ?? 4;
      for (let i = 1; i <= ahead; i += 1) {
        const next = index + i;
        if (next < opts.total && !cache.has(next)) void request(next);
      }
      return request(index);
    },
    has(index: number) {
      return cache.has(index);
    },
    stats() {
      return { cached: cache.size, inFlight: inFlight.size, fetches };
    },
    // 测试钩子：回放方向切换后清预取队列的观测（playhead 由 load 更新）。
    get _playhead() {
      return playhead;
    },
  } as TimelineLoader & { _playhead: number };
}

/**
 * 播放循环节流决策：给定上一帧渲染耗时与目标帧预算，决定本帧是否跳过
 * （渲染超预算时丢帧保帧率，而不是堆积任务）。
 */
export function shouldRenderFrame(
  lastRenderMs: number,
  budgetMs: number,
  sinceLastMs: number,
): boolean {
  // 距上次渲染不足 1/4 预算：必然是同一显示帧内的重复触发，跳过。
  if (sinceLastMs < budgetMs / 4) return false;
  // 上次渲染已超预算两倍：降级为隔帧渲染（保交互响应）。
  if (lastRenderMs > budgetMs * 2 && sinceLastMs < budgetMs) return false;
  return true;
}

/** 时间步标签格式化（ISO 截断到分钟；非 ISO 原样）。 */
export function formatStepLabel(t: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(t);
  return m ? `${m[1]} ${m[2]}` : t;
}
