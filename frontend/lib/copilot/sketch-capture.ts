/**
 * Sketch capture — 画布 copilot 手势捕获的纯函数层（ADR-0194）。
 *
 * 与 lib/edit/sketch-geometry 的区别：那边是「编辑用户草图要素」，
 * 这里是「向 Agent 表达意图」的指针序列 → 几何（不落草图图层）。
 * 全部纯函数、不碰地图实例 —— jsdom 可测（AGENTS 约定：捕获逻辑不进组件）。
 */

export type Pt = [number, number];

export interface RectLike {
  width: number;
  height: number;
}

/** client 坐标 → 覆层局部像素坐标。 */
export function pointerToLocal(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number },
): Pt {
  return [clientX - rect.left, clientY - rect.top];
}

/** 框选：start/end 两角 → 闭合 4 角环（逆时针归一，与 GeoJSON 外环惯例一致）。 */
export function boxRing(start: Pt, end: Pt): [number, number][] {
  const [x0, y0] = start;
  const [x1, y1] = end;
  const left = Math.min(x0, x1);
  const right = Math.max(x0, x1);
  const top = Math.min(y0, y1);
  const bottom = Math.max(y0, y1);
  return [
    [left, top],
    [right, top],
    [right, bottom],
    [left, bottom],
    [left, top],
  ];
}

/** 手绘圈选：指针轨迹降采样（minDistPx 内的点丢弃）并闭合。点数 < 3 → null。 */
export function freehandRing(points: Pt[], minDistPx = 4): Pt[] | null {
  const sampled: Pt[] = [];
  for (const p of points) {
    const last = sampled[sampled.length - 1];
    if (!last || Math.hypot(p[0] - last[0], p[1] - last[1]) >= minDistPx) {
      sampled.push(p);
    }
  }
  if (sampled.length < 3) return null;
  const [first, last] = [sampled[0], sampled[sampled.length - 1]];
  if (first[0] !== last[0] || first[1] !== last[1]) sampled.push(first);
  return sampled;
}

/** 多边形套索（点击序列）：直接闭合；可逐点注入吸附结果。点数 < 3 → null。 */
export function polygonLassoRing(points: Pt[]): Pt[] | null {
  if (points.length < 3) return null;
  const [first, last] = [points[0], points[points.length - 1]];
  const closed =
    first[0] === last[0] && first[1] === last[1] ? [...points] : [...points, first];
  return closed;
}

/* ─── 屏幕像素 → WGS84（web mercator 线性近似；意图级精度足够，spec §4.2）─── */

export interface MapViewLike {
  center: [number, number];
  zoom: number;
}

const EARTH_RADIUS_M = 6371000;
const TILE_SIZE_PX = 256;

function metersPerPixel(latDeg: number, zoom: number): number {
  return (
    (2 * Math.PI * EARTH_RADIUS_M * Math.cos((latDeg * Math.PI) / 180)) /
    (TILE_SIZE_PX * 2 ** zoom)
  );
}

/**
 * 覆层局部像素 → [lng, lat]。以 view.center 为原点做墨卡托线性展开
 * （与 maplibre project/unproject 的局部小范围一致性足够 copilot 意图使用；
 * 有真实 map 实例时组件应优先注入 project/unproject）。
 */
export function pxToLngLat(
  px: Pt,
  rect: RectLike,
  view: MapViewLike,
): [number, number] {
  const [lat0, lng0] = [view.center[1], view.center[0]];
  const mpp = metersPerPixel(lat0, view.zoom);
  const dx = px[0] - rect.width / 2;
  const dy = px[1] - rect.height / 2;
  const lng = lng0 + (dx * mpp) / (111320 * Math.cos((lat0 * Math.PI) / 180));
  const lat = lat0 - (dy * mpp) / 110540;
  return [lng, lat];
}

/** 像素环 → 经纬度环（逐点投影）。 */
export function ringPxToLngLat(
  ring: Pt[],
  rect: RectLike,
  view: MapViewLike,
): [number, number][] {
  return ring.map((p) => pxToLngLat(p, rect, view));
}

/** bbox（[w,s,e,n]）计算（经纬度环）。 */
export function ringBBox(
  ring: [number, number][],
): [number, number, number, number] {
  let w = Infinity;
  let s = Infinity;
  let e = -Infinity;
  let n = -Infinity;
  for (const [lng, lat] of ring) {
    w = Math.min(w, lng);
    s = Math.min(s, lat);
    e = Math.max(e, lng);
    n = Math.max(n, lat);
  }
  return [w, s, e, n];
}
