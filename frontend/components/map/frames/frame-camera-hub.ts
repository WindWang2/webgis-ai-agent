/**
 * G25 多画幅相机事件总线（P3）—— 把 G15 的 resolveGroupSync 纯函数接到
 * 真实 maplibre 相机事件。
 *
 * 同步关系（两类，均复用既有纯函数原语）：
 * 1. 主图 ↔ 活动画幅：双向 move → resolveSyncPair（ComparisonView 同门
 *    协议，pan/zoom 维度独立可控）；
 * 2. 帧间同步组：源帧 move → resolveGroupSync（同组未收敛成员批量补丁；
 *    异组/无组不传播）。
 *
 * 防回声三层（A07）：
 * - 纯函数层：resolveSyncPair/resolveGroupSync 容差收敛 → null（构造性断点）；
 * - 写入层：`applying` 代际门 —— hub 自己 jumpTo 期间触发的同步 move 全部
 *   吞掉（jumpTo→move 同步派发；与 ComparisonView isSyncing 同构，但泛化
 *   为多写者单门：任何 hub 写入期间的一切源事件都视为回声）；
 * - 实例层：每 entry 持 generation（context loss 重建 +1）；旧代际 entry
 *   的迟到事件被丢弃且自动解绑（A13 不把旧事件写到新帧）。
 *
 * 未挂载帧（P3 契约）：同组事件 → expectedCamera 记录（回调交给 grid，
 * 用于快照失效与激活初值），绝不为未挂载帧强行创建 context。
 *
 * 拒绝/降级：锁定 extent 的目标帧跳过相机写入（A17，纯函数守卫在
 * frame-runtime.cameraWriteGuard，hub 只做入口判定）；投影不兼容帧不入
 * hub（挂载前 checkProjectionCompat 已拒绝 —— A08）。
 */
import {
  applyCameraPatch,
  readCamera,
  resolveSyncPair,
  type CameraPatch,
  type CameraSnapshot,
} from '@/components/map/comparison/comparison-sync';
import { resolveGroupSync } from '@/lib/cartography/frame-sync';

/** 最小 maplibre 相机接口（readCamera/applyCameraPatch 的结构化依赖）。 */
export interface HubMapLike {
  on: (event: string, handler: () => void) => void;
  off: (event: string, handler: () => void) => void;
  getCenter: () => { lng: number; lat: number };
  getZoom: () => number;
  getBearing: () => number;
  getPitch: () => number;
  jumpTo: (opts: Record<string, unknown>) => unknown;
}

export interface HubAttachEntry {
  id: string;
  kind: 'main' | 'frame';
  map: HubMapLike;
  /** 'frame' 的同步组（'main' 恒 null —— 主图是工作台根视图，不属组）。 */
  syncGroup: string | null;
  /** 实例代际（context loss 重建 +1；旧代际自动失效）。 */
  generation: number;
  /** 锁定 extent → 拒绝一切相机写入（A17）。 */
  lockedExtent?: boolean;
}

export interface UnmountedFrameRef {
  frameId: string;
  syncGroup: string | null;
}

export interface FrameCameraHubOptions {
  syncPan: boolean;
  syncZoom: boolean;
  /** 主图↔活动画幅联动开关（默认 true；关闭则主图独立）。 */
  linkMain?: boolean;
  /** 未挂载同组帧的期望相机记录（grid 用：快照失效 + 激活初值）。 */
  onExpectedCamera?: (frameId: string, camera: CameraSnapshot, sourceId: string) => void;
}

interface HubEntry extends HubAttachEntry {
  handler: () => void;
}

export class FrameCameraHub {
  private entries = new Map<string, HubEntry>();
  private unmounted: UnmountedFrameRef[] = [];
  private applying = false;
  private disposed = false;
  private opts: FrameCameraHubOptions;
  /** 活动画幅 id（主图联动对端；null = 无联动）。 */
  private activeFrameId: string | null = null;

  constructor(options: FrameCameraHubOptions) {
    this.opts = { linkMain: true, ...options };
  }

  setSyncDimensions(pan: boolean, zoom: boolean): void {
    this.opts.syncPan = pan;
    this.opts.syncZoom = zoom;
  }

  setLinkMain(link: boolean): void {
    this.opts.linkMain = link;
  }

  /** 活动画幅切换（联动对端）。旧活动帧的事件不写入新帧（A22）。 */
  setActiveFrame(frameId: string | null): void {
    this.activeFrameId = frameId;
  }

  /** 未挂载帧登记（同组事件的期望相机记录对象；空数组清空）。 */
  noteUnmounted(frames: UnmountedFrameRef[]): void {
    this.unmounted = frames.filter((f) => !!f.frameId);
  }

  attach(entry: HubAttachEntry): () => void {
    if (this.disposed) return () => {};
    const key = entry.id;
    this.detach(key); // 旧代际/重复挂载先解绑（单槽位）
    const { id, generation } = entry;
    // 箭头词法捕获 this（no-this-alias 纪律）。
    const handler = () => this.onMove(id, generation);
    entry.map.on('move', handler);
    const stored: HubEntry = { ...entry, handler };
    this.entries.set(key, stored);
    return () => this.detach(key, entry.generation);
  }

  detach(id: string, generation?: number): void {
    const e = this.entries.get(id);
    if (!e) return;
    if (generation !== undefined && e.generation !== generation) return; // 新代际已接管
    e.map.off('move', e.handler);
    this.entries.delete(id);
  }

  /** 相机事件入口（attach 内部注册；测试可直接驱动）。 */
  onMove(sourceId: string, generation: number): void {
    if (this.disposed || this.applying) return; // 回声门 + 陈旧入口
    const source = this.entries.get(sourceId);
    if (!source || source.generation !== generation) return; // 旧代际迟到事件
    const sourceCamera = readCamera(source.map);
    const writes: Array<{ map: HubMapLike; patch: CameraPatch; id: string }> = [];
    // ① 主图 ↔ 活动画幅（双向，对端 = 另一侧）。
    if (this.opts.linkMain && this.activeFrameId) {
      let other: HubEntry | undefined;
      if (source.kind === 'main') {
        other = this.entries.get(this.activeFrameId);
      } else if (source.id === this.activeFrameId) {
        other = this.mainEntry();
      }
      if (other && other.id !== sourceId && !other.lockedExtent) {
        const patch = resolveSyncPair(sourceCamera, readCamera(other.map), this.opts.syncPan, this.opts.syncZoom);
        if (patch) writes.push({ map: other.map, patch, id: other.id });
      }
    }
    // ② 帧间同步组（同组批量；主图不属组）。
    if (source.kind === 'frame' && source.syncGroup) {
      const targets = [...this.entries.values()].map((e) => ({
        id: e.id,
        map: e.map as Parameters<typeof readCamera>[0],
        syncGroup: e.syncGroup,
        camera: readCamera(e.map),
      }));
      const outcome = resolveGroupSync(sourceId, sourceCamera, targets, this.opts.syncPan, this.opts.syncZoom);
      for (const p of outcome.patches) {
        const t = this.entries.get(p.id);
        if (t && !t.lockedExtent) writes.push({ map: t.map, patch: p.patch, id: t.id });
      }
    }
    // 应用（单一 applying 门 —— 期间一切 move 均为回声）。
    if (writes.length > 0) {
      this.applying = true;
      try {
        for (const w of writes) applyCameraPatch(w.map, w.patch);
      } finally {
        this.applying = false;
      }
    }
    // ③ 未挂载同组帧：期望相机记录（不创建 context）。
    if (source.kind === 'frame' && source.syncGroup) {
      for (const u of this.unmounted) {
        if (u.syncGroup === source.syncGroup && u.frameId !== sourceId) {
          this.opts.onExpectedCamera?.(u.frameId, sourceCamera, sourceId);
        }
      }
    }
  }

  private mainEntry(): HubEntry | undefined {
    for (const e of this.entries.values()) if (e.kind === 'main') return e;
    return undefined;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const e of this.entries.values()) e.map.off('move', e.handler);
    this.entries.clear();
    this.unmounted = [];
  }

  /** 诊断/测试：当前登记的活成员（refs-only）。 */
  memberIds(): string[] {
    return [...this.entries.keys()];
  }

  /** id 当前绑定的 map 实例（context loss 重建检测用；未登记 null）。 */
  entryMap(id: string): HubMapLike | null {
    return this.entries.get(id)?.map ?? null;
  }
}
