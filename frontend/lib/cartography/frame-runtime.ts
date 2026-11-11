/**
 * G25 多画幅运行时契约（P0）—— 从持久 frame 产品生成唯一只读运行时投影。
 *
 * 纪律（与 frame-product.ts / frame-sync.ts 同门）：
 * - 单一真相 = committed MapSpec（frame_product + layout.frames）；本模块
 *   只做**运行时**投影（挂载状态、版本匹配、相机草稿/期望、快照指纹、
 *   实例代际），不另存一套 frames —— slots 由 spec 每次推导，运行时输入
 *   （mounted/snapshot/camera）都是外置的观察面，可被随时重算；
 * - 状态词表封闭：unmounted / loading / live / snapshot / stale / error。
 *   **snapshot 永不宣称 live**；resolved revision 落后 desired → stale
 *   （旧 revision 不得 verified —— A11/A12 的构造性基础）；
 * - camera 三层：applied（地图实况，不入本模块）/ expected（未挂载帧的
 *   同组期望，hub 写入）/ draft（用户草稿，sessionStorage 客户端本地，
 *   永不进 schema、永不 CAS 写 —— "不能无意覆盖他人 revision"）。
 */
import type { CameraSnapshot } from '@/lib/cartography/frame-sync';
import type { FrameProductView } from '@/lib/cartography/frame-product';
import { projectFrameProduct } from '@/lib/cartography/frame-product';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

/** 画幅挂载状态（词表封闭；UI/观察/测试共用同一词表）。 */
export type FrameMountState =
  | 'unmounted' // 无活实例、无匹配快照（含 never-mounted 的 not_observed 占位）
  | 'loading' // 活跃/预热槽位已分配，地图实例创建或首帧 reconcile 中
  | 'live' // 活实例 + 版本匹配（resolved == desired）
  | 'snapshot' // 非活实例，显示指纹匹配的快照（显式非 live）
  | 'stale' // 有实例或快照，但 resolved 版本落后 desired（不得 verified）
  | 'error'; // 实例/解析失败（局部可重试，不影响其他画幅）

export interface FrameViewportSize {
  width: number;
  height: number;
}

/** 挂载帧的运行时登记（FrameViewport 上报；观察面非真相）。 */
export interface MountedFrameFact {
  frameId: string;
  /** 实例代际：context loss 重建后 +1（旧代际事件/快照全部作废）。 */
  generation: number;
  resolvedSpecRevision: number;
  resolvedDataRevision: number | null;
  /** reconcile/source 错误码（null = 无错）。 */
  errorCode: string | null;
  errorMessage?: string;
}

export interface SnapshotFact {
  frameId: string;
  fingerprint: string;
  specRevision: number;
  dataRevision: number | null;
  viewportSize: FrameViewportSize | null;
}

/** 运行时投影的观察输入（全部可随时重算的外置事实）。 */
export interface FrameRuntimeInputs {
  spec: MapSpec | null | undefined;
  /** 当前 live generation（session-cursor 的 mapspec live generation）。 */
  specRevision: number;
  /** 运行时激活的画幅（初始 = product.active_frame，之后用户切换）。 */
  activeFrameId: string | null;
  mounted: ReadonlyMap<string, MountedFrameFact>;
  snapshots: ReadonlyMap<string, SnapshotFact>;
  cameraDrafts: ReadonlyMap<string, CameraSnapshot>;
  expectedCameras: ReadonlyMap<string, CameraSnapshot>;
  /** 网格瓦片尺寸（快照指纹维度；null = 未知，快照不判 stale-by-size）。 */
  viewportSize: FrameViewportSize | null;
}

export interface FrameRuntimeSlotView {
  frameId: string;
  title: string;
  order: number;
  enabled: boolean;
  syncGroup: string | null;
  active: boolean;
  mountState: FrameMountState;
  desiredSpecRevision: number;
  resolvedSpecRevision: number | null;
  resolvedDataRevision: number | null;
  desiredDataRevision: number | null;
  /** 期望/草稿相机（激活挂载时的初值：draft 优先，其次 expected）。 */
  cameraDraft: CameraSnapshot | null;
  expectedCamera: CameraSnapshot | null;
  snapshotFingerprint: string | null;
  /** 快照与当前期望版本是否逐位匹配（snapshot 态的必要条件）。 */
  snapshotCurrent: boolean;
  generation: number;
  error: { code: string; message: string } | null;
  /** 帧槽原始数据绑定（FrameViewport 过滤输入；raw 同 FrameProductSlotView.raw）。 */
  raw: Record<string, unknown>;
}

/** stable 字符串 hash（FNV-1a 32bit → hex；缓存键用途，非安全 hash）。 */
export function stableHash(input: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, '0');
}

/** 快照指纹：session|frame|specRev|dataRev|w×h（同输入 ⇒ 同指纹）。 */
export function snapshotFingerprint(input: {
  sessionId: string;
  frameId: string;
  specRevision: number;
  dataRevision: number | null;
  viewportSize: FrameViewportSize | null;
}): string {
  const size = input.viewportSize
    ? `${Math.round(input.viewportSize.width)}x${Math.round(input.viewportSize.height)}`
    : 'any';
  return stableHash(
    `${input.sessionId}|${input.frameId}|${input.specRevision}|${
      input.dataRevision ?? 'none'
    }|${size}`,
  );
}

/** 数据面投影复用 G15（不重复实现产品语义）。 */
function productViewOf(spec: MapSpec | null | undefined): FrameProductView {
  return projectFrameProduct(spec);
}

/**
 * 唯一只读运行时投影：spec × 观察输入 → 每帧槽位视图。
 * 不在 spec 里的 mounted/snapshot/draft 条目被忽略（frame 删除 → 身份消失，
 * 迟到的快照/事件不得复活它 —— A13）。
 */
export function projectFrameRuntime(inputs: FrameRuntimeInputs): FrameRuntimeSlotView[] {
  const product = productViewOf(inputs.spec);
  if (!product.bound) return [];
  const layoutFrames = Array.isArray(inputs.spec?.layout?.frames)
    ? inputs.spec!.layout!.frames!
    : [];
  const enabledById = new Map<string, boolean>();
  for (const lf of layoutFrames) {
    if (lf && typeof lf.id === 'string') {
      enabledById.set(lf.id, lf.enabled !== false);
    }
  }
  return product.slots.map((slot, order) => {
    const mounted = inputs.mounted.get(slot.frameId) ?? null;
    const snap = inputs.snapshots.get(slot.frameId) ?? null;
    const desiredDataRevision = slot.binding?.contentRevision ?? null;
    const active = inputs.activeFrameId === slot.frameId;
    const desiredFp = snap
      ? snapshotFingerprint({
          sessionId: '',
          frameId: slot.frameId,
          specRevision: inputs.specRevision,
          dataRevision: desiredDataRevision,
          viewportSize: inputs.viewportSize,
        })
      : null;
    // 快照"当前"判定：条目版本 == 期望版本（指纹在 store 侧键控；此处按
    // 版本比较，使 spec/数据刷新后旧快照立即 stale —— A11）。
    const snapshotCurrent = !!(
      snap &&
      snap.specRevision === inputs.specRevision &&
      (snap.dataRevision ?? null) === desiredDataRevision &&
      (!inputs.viewportSize ||
        !snap.viewportSize ||
        (snap.viewportSize.width === inputs.viewportSize.width &&
          snap.viewportSize.height === inputs.viewportSize.height))
    );
    let mountState: FrameMountState;
    let error: FrameRuntimeSlotView['error'] = null;
    if (active) {
      if (mounted && mounted.errorCode) {
        mountState = 'error';
        error = { code: mounted.errorCode, message: mounted.errorMessage ?? '' };
      } else if (!mounted) {
        mountState = 'loading';
      } else if (
        mounted.resolvedSpecRevision === inputs.specRevision &&
        (mounted.resolvedDataRevision ?? null) === desiredDataRevision
      ) {
        mountState = 'live';
      } else {
        mountState = 'stale'; // 活实例仍在旧版本（reconcile 进行中）
      }
    } else if (snap) {
      mountState = snapshotCurrent ? 'snapshot' : 'stale';
    } else {
      mountState = 'unmounted';
    }
    return {
      frameId: slot.frameId,
      title: slot.title,
      order,
      enabled: enabledById.get(slot.frameId) ?? slot.enabled,
      syncGroup: slot.syncGroup,
      active,
      mountState,
      desiredSpecRevision: inputs.specRevision,
      resolvedSpecRevision: mounted?.resolvedSpecRevision ?? snap?.specRevision ?? null,
      resolvedDataRevision: mounted?.resolvedDataRevision ?? snap?.dataRevision ?? null,
      desiredDataRevision,
      cameraDraft: inputs.cameraDrafts.get(slot.frameId) ?? null,
      expectedCamera: inputs.expectedCameras.get(slot.frameId) ?? null,
      snapshotFingerprint: snap?.fingerprint ?? null,
      snapshotCurrent,
      generation: mounted?.generation ?? 0,
      error,
      raw: slot.raw,
    };
  });
}

/**
 * active frame 失效（删除/禁用/解绑/隐藏）后的确定性后继：
 * 产品序中被删者的下一个可用（enabled 且在场）帧，环形回绕；无 → null。
 * 确定性：同输入同输出（A20/A22 的可复现基础）。
 */
export function selectActiveSuccessor(
  orderedFrameIds: readonly string[],
  removedId: string | null,
  isEnabled: (frameId: string) => boolean = () => true,
): string | null {
  const ids = orderedFrameIds.filter((id) => id !== removedId && isEnabled(id));
  if (ids.length === 0) return null;
  if (!removedId) return ids[0];
  const idx = orderedFrameIds.indexOf(removedId);
  if (idx < 0) return ids[0];
  // 从被删者原位置的下一个可用帧开始环形查找。
  for (let step = idx + 1; step < orderedFrameIds.length + idx + 1; step++) {
    const cand = orderedFrameIds[step % orderedFrameIds.length];
    if (cand !== removedId && isEnabled(cand)) return cand;
  }
  return null;
}

/** 相机写入守卫：锁定 extent（layout.frames[].extent）→ 拒绝同步写。 */
export interface CameraWriteGuard {
  ok: boolean;
  reason: 'locked_extent' | 'ok';
  extent?: [number, number, number, number];
}

export function cameraWriteGuard(frame: {
  id?: string;
  extent?: [number, number, number, number];
} | null): CameraWriteGuard {
  if (frame && Array.isArray(frame.extent) && frame.extent.length === 4) {
    return { ok: false, reason: 'locked_extent', extent: frame.extent };
  }
  return { ok: true, reason: 'ok' };
}

/**
 * 投影/兼容性拒绝（A08）：MapSpec 运行时全 3857；帧级投影差异只能来自
 * 数据绑定 ref 的 CRS（后端 ingest 责任面）。本守卫在相机同步与帧挂载前
 * 做显式判定 —— 不兼容 → 明确拒绝（含原因），绝不复制坐标。
 * projectionB 为空（未声明）视为与宿主一致（旧数据兼容）。
 */
export function checkProjectionCompat(
  frameProjection: string | null | undefined,
  hostProjection: string | null | undefined,
): { ok: boolean; reason: string | null } {
  const a = frameProjection || 'EPSG:3857';
  const b = hostProjection || 'EPSG:3857';
  return a === b ? { ok: true, reason: null } : { ok: false, reason: `projection_mismatch:${a}!=${b}` };
}
