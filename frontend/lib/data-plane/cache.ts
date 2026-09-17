/**
 * RefDataCache — 数据面 ref→FeatureCollection 的字节有界 LRU（extreme-scale v2）。
 *
 * 统一此前散落在各拉取点的临时缓存（ref-source-resolver 的 24 条 LRU、
 * layer-data 的 pendingHydrations、session restore 的无缓存并发拉起）背后
 * 的公共预算语义：
 *
 * - **字节预算**：条目按真实估算字节入账，超预算触发逐出（Oracle：
 *   内存达到预算时可预测逐出且地图可用）。
 * - **可见性 pin（预留）**：pin 语义已实现并测试锁定，但本期接线未消费
 *   —— 接入「可见层 pin / 隐藏层 unpin」生命周期后，预算压力才不会逐出
 *   正在显示的层数据。全部被 pin 且超预算时缓存如实超账（不装绿）。
 * - **巨物拒绝**：单条超过总预算的条目不入缓存（调用方仍可直通使用），
 *   防止一条巨型 ref 把全部存活数据冲掉。
 *
 * 纯客户端显示通道缓存：分析事实（全量 FC 按需拉取）不经过本缓存的
 * 逐出语义影响 —— 逐出只意味着「显示副本释放，需要时再拉」。
 */

import type { FeatureCollectionLike } from '@/lib/mapspec-runtime/source-diff';

export interface RefCacheEntry {
  fc: FeatureCollectionLike;
  etag?: string;
  bytes: number;
  pinned: boolean;
  /** 写入时刻（调度器新鲜窗 TTL 的判据）。 */
  fetchedAt: number;
}

export interface RefCacheSetOpts {
  etag?: string;
  /** 显式字节数；缺省时用 estimateFeatureCollectionBytes 估算。 */
  bytes?: number;
}

export interface RefDataCacheOpts {
  maxBytes: number;
  maxEntries?: number;
  onEvict?: (key: string, entry: RefCacheEntry) => void;
  now?: () => number;
}

/** 粗粒度确定性字节估算：特征数 × 每特征常数 + 结构底价。
 *  显示通道预算不需要精确序列化字节 —— 需要的是**单调、确定性、
 *  与规模正相关**的信号；精确值只有服务端知道。 */
const ESTIMATED_BYTES_PER_FEATURE = 200;
const ESTIMATED_BASE_BYTES = 512;

export function estimateFeatureCollectionBytes(fc: FeatureCollectionLike | undefined | null): number {
  if (!fc || !Array.isArray(fc.features)) return 0;
  return ESTIMATED_BASE_BYTES + fc.features.length * ESTIMATED_BYTES_PER_FEATURE;
}

export class RefDataCache {
  private readonly maxBytes: number;
  private readonly maxEntries: number;
  private readonly onEvict?: (key: string, entry: RefCacheEntry) => void;
  private readonly now: () => number;
  private readonly entries = new Map<string, RefCacheEntry>();
  private _totalBytes = 0;

  constructor(opts: RefDataCacheOpts) {
    this.maxBytes = Math.max(1, Math.floor(opts.maxBytes));
    this.maxEntries = Math.max(1, Math.floor(opts.maxEntries ?? 64));
    this.onEvict = opts.onEvict;
    this.now = opts.now ?? (() => Date.now());
  }

  get totalBytes(): number {
    return this._totalBytes;
  }

  get size(): number {
    return this.entries.size;
  }

  /** LRU 序（最老在前）的键快照。 */
  keys(): string[] {
    return [...this.entries.keys()];
  }

  /** 命中并触摸（刷新 recency）。 */
  get(key: string): RefCacheEntry | undefined {
    const entry = this.entries.get(key);
    if (!entry) return undefined;
    this.entries.delete(key);
    this.entries.set(key, entry);
    return entry;
  }

  /** 命中但不触摸（探测/审计用）。 */
  peek(key: string): RefCacheEntry | undefined {
    return this.entries.get(key);
  }

  set(key: string, fc: FeatureCollectionLike, opts: RefCacheSetOpts = {}): void {
    const bytes = Math.max(
      0,
      Math.floor(opts.bytes ?? estimateFeatureCollectionBytes(fc)),
    );
    // 巨物拒绝：单条超总预算不入缓存（否则逐出它毫无意义、还冲掉别人）。
    if (bytes > this.maxBytes) return;
    // 同 key 覆盖：先按 delete 语义结账再插入。
    this.delete(key);
    this.entries.set(key, {
      fc,
      etag: opts.etag,
      bytes,
      pinned: false,
      fetchedAt: this.now(),
    });
    this._totalBytes += bytes;
    this.evictLocked(key);
  }

  /** 可见性 pin：true 期间豁免自动逐出。返回是否确有该条目。
   *  unpin 本身不触发对刚 unpin 条目的逐出（可预测性：解除 pin 与被逐
   *  之间隔一次后续写入，接线方不会遇到 unpin-即-消失的闪烁）。 */
  setPinned(key: string, pinned: boolean): boolean {
    const entry = this.entries.get(key);
    if (!entry) return false;
    entry.pinned = pinned;
    if (!pinned) this.evictLocked(key);
    return true;
  }

  delete(key: string): boolean {
    const entry = this.entries.get(key);
    if (!entry) return false;
    this.entries.delete(key);
    this._totalBytes -= entry.bytes;
    return true;
  }

  clear(): void {
    this.entries.clear();
    this._totalBytes = 0;
  }

  /** 超预算逐出：仅未 pin、最老优先；新插入的 key 豁免一轮（刚写入就被
   *  自己触发的逐出清掉是调用方不可推理的）。仍超账则如实保留。 */
  private evictLocked(justInserted?: string): void {
    if (
      this._totalBytes <= this.maxBytes
      && this.entries.size <= this.maxEntries
    ) {
      return;
    }
    for (const key of [...this.entries.keys()]) {
      if (
        this._totalBytes <= this.maxBytes
        && this.entries.size <= this.maxEntries
      ) {
        break;
      }
      if (key === justInserted) continue;
      const entry = this.entries.get(key);
      if (!entry || entry.pinned) continue;
      this.entries.delete(key);
      this._totalBytes -= entry.bytes;
      try {
        this.onEvict?.(key, entry);
      } catch {
        // 观察者失败绝不阻断逐出（预算纪律优先）。
      }
    }
  }
}
