/**
 * G25 快照 LRU store（P1/P4）—— 画幅卸载捕获的 canvas 快照缓存。
 *
 * 契约：
 * - 每帧至多一条快照（keyed by frameId），项数上限默认 12（MAX_PLAN_FRAMES
 *   同值）+ 字节上限默认 32MiB —— 超限 LRU 逐出最久未用；
 * - `get` 按 fingerprint 命中：session+frame+specRev+dataRev+viewport 任一
 *   维度变化 → miss（A11 缓存按 revision 失效的 store 侧执行点）；
 * - 快照携带 sessionId（A14：会话切换 invalidateSession 全清，无残留）；
 * - 巨幅快照（单条超限）拒绝入库（rejected_oversize），不挤掉他人；
 * - store 只存 dataUrl 字符串与元数据 —— 不持 Map 实例/canvas 引用
 *   （与 comparison-export-registry 同一防泄漏纪律）。
 */

export interface FrameSnapshotEntry {
  sessionId: string;
  frameId: string;
  fingerprint: string;
  dataUrl: string;
  bytes: number;
  specRevision: number;
  dataRevision: number | null;
  viewportSize: { width: number; height: number };
  capturedAt: number;
  /** 捕获时的相机（激活初值与漂移判定的参照）。 */
  camera: { center: [number, number]; zoom: number; bearing: number; pitch: number } | null;
}

export const DEFAULT_SNAPSHOT_MAX_ITEMS = 12;
export const DEFAULT_SNAPSHOT_MAX_BYTES = 32 * 1024 * 1024;

export type SnapshotPutOutcome = 'stored' | 'rejected_oversize' | 'rejected_empty';

type Listener = () => void;

export class FrameSnapshotStore {
  private entries = new Map<string, FrameSnapshotEntry>(); // frameId → entry
  private order: string[] = []; // LRU 顺序（尾部 = 最新使用）
  private totalBytes = 0;
  private hits = 0;
  private misses = 0;
  private readonly maxItems: number;
  private readonly maxBytes: number;
  private listeners = new Set<Listener>();

  constructor(
    maxItems = DEFAULT_SNAPSHOT_MAX_ITEMS,
    maxBytes = DEFAULT_SNAPSHOT_MAX_BYTES,
  ) {
    this.maxItems = Math.max(1, maxItems);
    this.maxBytes = Math.max(1, maxBytes);
  }

  /** 存入/覆盖一帧快照（逐出至预算内；单条超限拒绝）。 */
  put(entry: FrameSnapshotEntry): SnapshotPutOutcome {
    if (!entry.dataUrl || entry.bytes <= 0) return 'rejected_empty';
    if (entry.bytes > this.maxBytes) return 'rejected_oversize';
    const prev = this.entries.get(entry.frameId);
    if (prev) {
      this.totalBytes -= prev.bytes;
      this.entries.delete(entry.frameId);
      this.order = this.order.filter((id) => id !== entry.frameId);
    }
    // 项数/字节预算（新条目入队后从 LRU 头逐出）。
    this.entries.set(entry.frameId, { ...entry });
    this.order.push(entry.frameId);
    this.totalBytes += entry.bytes;
    while (this.order.length > this.maxItems) this.evictOldest();
    while (this.totalBytes > this.maxBytes && this.order.length > 1) this.evictOldest();
    this.emit();
    return 'stored';
  }

  /** 指纹命中取出（touch 更新 LRU 位）。 */
  get(frameId: string, fingerprint: string): FrameSnapshotEntry | null {
    const e = this.entries.get(frameId);
    if (!e || e.fingerprint !== fingerprint) {
      this.misses += 1;
      return null;
    }
    this.hits += 1;
    this.touch(frameId);
    return e;
  }

  /** 无 touch 窥视（投影用）。 */
  peek(frameId: string): FrameSnapshotEntry | null {
    return this.entries.get(frameId) ?? null;
  }

  invalidateFrame(frameId: string): void {
    const e = this.entries.get(frameId);
    if (!e) return;
    this.totalBytes -= e.bytes;
    this.entries.delete(frameId);
    this.order = this.order.filter((id) => id !== frameId);
    this.emit();
  }

  /** 会话切换全清（A14）。 */
  invalidateSession(sessionId: string): void {
    for (const id of [...this.order]) {
      const e = this.entries.get(id);
      if (e && e.sessionId === sessionId) {
        this.totalBytes -= e.bytes;
        this.entries.delete(id);
      }
    }
    this.order = this.order.filter((id) => this.entries.has(id));
    this.emit();
  }

  stats(): { count: number; bytes: number; maxItems: number; maxBytes: number; hits: number; misses: number } {
    return {
      count: this.entries.size,
      bytes: this.totalBytes,
      maxItems: this.maxItems,
      maxBytes: this.maxBytes,
      hits: this.hits,
      misses: this.misses,
    };
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private touch(frameId: string): void {
    this.order = this.order.filter((id) => id !== frameId);
    this.order.push(frameId);
  }

  private evictOldest(): void {
    const oldest = this.order.shift();
    if (oldest === undefined) return;
    const e = this.entries.get(oldest);
    if (e) this.totalBytes -= e.bytes;
    this.entries.delete(oldest);
  }

  private emit(): void {
    for (const l of this.listeners) l();
  }
}
