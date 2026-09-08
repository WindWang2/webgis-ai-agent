/**
 * ComparisonView 的相机同步纯函数（Wave 8 对比工作区）。
 *
 * 纪律：双图同步最容易写出「A move → 写 B → B move → 写 A」的反馈环。
 * 这里把同步决策收敛为纯函数 `resolveSyncPair`：
 * - 返回 `null` 表示「无需同步」（目标相机在启用维度上已收敛）——
 *   即同一事件重放第二次是 no-op，纯函数层面保证幂等收敛；
 * - 运行时的 isSyncing ref 标志负责事件层面的防环（写相机期间触发的事件
 *   全部丢弃），两层防御互为兜底。
 * syncPan / syncZoom 来自 ComparisonState（workbenchSlice），关闭的维度
 * 不产生任何 patch 键。
 */

/** 相机快照（与 MapLibre getCenter/getZoom/getBearing/getPitch 对齐）。 */
export interface CameraSnapshot {
  center: [number, number];
  zoom: number;
  bearing: number;
  pitch: number;
}

/** 对比 layer id 的约定：spec 层族 id（HUD 行优先 _mapspecLayerId，否则行 id）。
 *  入口（layers-tab）与消费方（ComparisonView 的层族过滤）必须同源此定义。 */
export function comparisonFamilyId(
  layer: { id: string; _mapspecLayerId?: string | null },
): string {
  return layer._mapspecLayerId || layer.id;
}

/** 相机补丁（jumpTo 载荷的子集）。 */
export type CameraPatch = Partial<CameraSnapshot>;

/** 相等判定容差：浮点相机值经 MapLibre 内部三角/瓦片换算可能带 1e-9 量级噪声。 */
const EPSILON = 1e-7;

/**
 * 由 source 相机推导 target 应补齐的相机补丁；在启用的同步维度上已一致
 * （含容差）则返回 null（幂等：二次应用必收敛为 null，不产生反馈环）。
 */
export function resolveSyncPair(
  source: CameraSnapshot,
  target: CameraSnapshot,
  syncPan: boolean,
  syncZoom: boolean,
): CameraPatch | null {
  if (!syncPan && !syncZoom) return null;
  const patch: CameraPatch = {};
  let changed = false;
  if (syncPan) {
    if (
      Math.abs(source.center[0] - target.center[0]) > EPSILON ||
      Math.abs(source.center[1] - target.center[1]) > EPSILON
    ) {
      patch.center = [source.center[0], source.center[1]];
      changed = true;
    }
    if (Math.abs(source.bearing - target.bearing) > EPSILON) {
      patch.bearing = source.bearing;
      changed = true;
    }
    if (Math.abs(source.pitch - target.pitch) > EPSILON) {
      patch.pitch = source.pitch;
      changed = true;
    }
  }
  if (syncZoom && Math.abs(source.zoom - target.zoom) > EPSILON) {
    patch.zoom = source.zoom;
    changed = true;
  }
  return changed ? patch : null;
}

/**
 * swipe 分割位置夹取：契约 0..1（视口宽度比例）。NaN（拖拽事件异常载荷）
 * 收敛为 0 —— 绝不把 NaN 写进 store；±Infinity 走常规夹取。
 */
export function clampSwipePosition(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/** 键盘步进（ArrowLeft/Right ±0.02，role=slider 的 step 契约）。 */
export const SWIPE_KEYBOARD_STEP = 0.02;

/** MapLibre 实例 → 快照（IO 适配层；同步决策只走纯函数）。 */
export function readCamera(map: {
  getCenter: () => { lng: number; lat: number };
  getZoom: () => number;
  getBearing: () => number;
  getPitch: () => number;
}): CameraSnapshot {
  const center = map.getCenter();
  return {
    center: [center.lng, center.lat],
    zoom: map.getZoom(),
    bearing: map.getBearing() ?? 0,
    pitch: map.getPitch() ?? 0,
  };
}

/** 把 resolveSyncPair 的补丁应用到 MapLibre 实例（jumpTo：瞬时、无动画）。 */
export function applyCameraPatch(
  map: { jumpTo: (opts: Record<string, unknown>) => unknown },
  patch: CameraPatch,
): void {
  if (!patch.center && patch.zoom === undefined && patch.bearing === undefined && patch.pitch === undefined) {
    return;
  }
  map.jumpTo(patch as Record<string, unknown>);
}
