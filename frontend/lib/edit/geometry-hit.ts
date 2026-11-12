/**
 * geometry-hit — 要素精确命中（G24 / P2）。
 *
 * 语义（验收锚点）：
 * - A08 Polygon 洞内不算命中（外环内、洞内 → 未命中）；
 * - A09 长线段中段可命中（线命中 = 逐段最短距离 ≤ 容差，不是只有顶点）；
 * - A10 Multi* 按真实部件处理（MultiLine=多折线段距、MultiPolygon=多面
 *   各自外环减洞），不再退化为顶点近邻；
 * - 距离全部在**当前视口投影像素**上计算（project 由调用方注入 map.project）。
 *
 * 纯函数：不触 MapLibre、不发请求 —— 单测可直接钉住。
 */

export type Projector = (p: [number, number]) => { x: number; y: number };

/** 点到线段的最短距离（px）。退化段（零长）= 点距。 */
export function distToSegmentPx(
  p: { x: number; y: number },
  a: { x: number; y: number },
  b: { x: number; y: number },
): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  let t = 0;
  if (len2 > 0) {
    t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
  }
  const cx = a.x + t * dx;
  const cy = a.y + t * dy;
  return Math.hypot(p.x - cx, p.y - cy);
}

/** 折线的最短命中距离（px）＝ min(逐段距离)。 */
export function distToPolylinePx(
  point: { x: number; y: number },
  coords: [number, number][],
  project: Projector,
): number {
  if (coords.length === 1) {
    const p = project(coords[0]);
    return Math.hypot(p.x - point.x, p.y - point.y);
  }
  let best = Infinity;
  for (let i = 0; i + 1 < coords.length; i++) {
    const a = project(coords[i]);
    const b = project(coords[i + 1]);
    best = Math.min(best, distToSegmentPx(point, a, b));
  }
  return best;
}

/** 射线法：点是否在环内（WGS84 经纬度直算 —— 与容差像素共同吸收边界近似）。 */
export function pointInRing(lng: number, lat: number, ring: [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

type Part =
  | { kind: 'point'; coords: [number, number][] }
  | { kind: 'line'; coords: [number, number][] }
  | { kind: 'polygon'; rings: [number, number][][] };

/** 任意几何 → 命中部件（Point/MultiPoint/Line/MultiLine/Polygon/MultiPolygon）。 */
export function hitParts(geometry: GeoJSON.Geometry): Part[] {
  switch (geometry.type) {
    case 'Point':
      return [{ kind: 'point', coords: [geometry.coordinates as [number, number]] }];
    case 'MultiPoint':
      return [{ kind: 'point', coords: geometry.coordinates as [number, number][] }];
    case 'LineString':
      return [{ kind: 'line', coords: geometry.coordinates as [number, number][] }];
    case 'MultiLineString':
      return (geometry.coordinates as [number, number][][]).map((coords) => ({ kind: 'line' as const, coords }));
    case 'Polygon':
      return [{ kind: 'polygon', rings: geometry.coordinates as [number, number][][] }];
    case 'MultiPolygon': {
      const parts: Part[] = [];
      for (const poly of geometry.coordinates as [number, number][][][]) {
        parts.push({ kind: 'polygon', rings: poly });
      }
      return parts;
    }
    default:
      return [];
  }
}

export interface HitResult {
  hit: boolean;
  /** 命中距离（px；面内命中 = 0）；未命中 = Infinity。 */
  distPx: number;
}

/**
 * 要素精确命中：面 = 外环内减洞（命中距离 0）；线 = 逐段最短距离；
 * 点 = 顶点距离。Multi* 取各部件最优。GeometryCollection 不命中（诚实）。
 */
export function preciseFeatureHit(
  feature: GeoJSON.Feature,
  lngLat: [number, number],
  project: Projector,
  point: { x: number; y: number },
  tolerancePx: number,
): HitResult {
  const g = feature.geometry;
  if (!g) return { hit: false, distPx: Infinity };
  let best = Infinity;
  for (const part of hitParts(g)) {
    if (part.kind === 'point') {
      for (const c of part.coords) {
        const p = project(c);
        best = Math.min(best, Math.hypot(p.x - point.x, p.y - point.y));
      }
    } else if (part.kind === 'line') {
      best = Math.min(best, distToPolylinePx(point, part.coords, project));
    } else {
      // polygon：外环内且不在任何洞内 → 命中（距离 0）。洞是「不属于该面」
      // 的区域 —— 洞内点击绝不允许被边界邻近救回（A08：Polygon hole 不误
      // 命中）；外环外同理不做顶点/边近邻回退（与既有内联路径语义一致）。
      const [outer, ...holes] = part.rings;
      if (!outer) continue;
      if (pointInRing(lngLat[0], lngLat[1], outer)) {
        let inHole = false;
        for (const hole of holes) {
          if (pointInRing(lngLat[0], lngLat[1], hole)) { inHole = true; break; }
        }
        if (!inHole) best = 0;
      }
    }
  }
  return { hit: best <= tolerancePx, distPx: best };
}
