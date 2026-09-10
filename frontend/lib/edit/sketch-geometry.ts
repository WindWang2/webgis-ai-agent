/**
 * Sketch geometry helpers — 草图编辑的纯几何函数（可单测，不碰地图实例）。
 */
import type { Geometry } from 'geojson';
import type { SketchFeature } from './sketch-store';

/** 要素所有顶点（Point/LineString/Polygon 外环 + 内环）。 */
export function geometryVertices(geometry: Geometry): [number, number][] {
  const out: [number, number][] = [];
  const walk = (coords: unknown): void => {
    if (!Array.isArray(coords)) return;
    if (typeof coords[0] === 'number' && typeof coords[1] === 'number') {
      out.push([coords[0] as number, coords[1] as number]);
      return;
    }
    for (const c of coords) walk(c);
  };
  switch (geometry.type) {
    case 'Point': out.push([geometry.coordinates[0], geometry.coordinates[1]]); break;
    case 'MultiPoint':
    case 'LineString': walk(geometry.coordinates); break;
    case 'Polygon':
    case 'MultiLineString': walk(geometry.coordinates); break;
    case 'MultiPolygon': walk(geometry.coordinates); break;
  }
  return out;
}

/**
 * 屏幕空间吸附：在 candidates 中找距 pt 最近的顶点。
 * project/unproject 为 map.project/unproject（像素 ↔ 经纬度）；返回
 * 命中的经纬度坐标与像素距离，超出 maxPx 返回 null。
 */
export function snapToVertex(
  pt: [number, number],
  candidates: [number, number][],
  project: (lngLat: [number, number]) => { x: number; y: number },
  unproject: (point: { x: number; y: number }) => { lng: number; lat: number },
  maxPx = 12,
): { coordinate: [number, number]; distancePx: number } | null {
  let best: { coordinate: [number, number]; distancePx: number } | null = null;
  const p0 = project(pt);
  for (const candidate of candidates) {
    const pc = project(candidate);
    const distancePx = Math.hypot(pc.x - p0.x, pc.y - p0.y);
    if (distancePx <= maxPx && (best === null || distancePx < best.distancePx)) {
      best = { coordinate: candidate, distancePx };
    }
  }
  return best;
}

/** 闭合环（首尾不同则补首点）。 */
export function closeRing(ring: [number, number][]): [number, number][] {
  if (ring.length < 3) return ring;
  const [first, last] = [ring[0], ring[ring.length - 1]];
  return first[0] === last[0] && first[1] === last[1] ? ring : [...ring, first];
}

/** 草稿 → 完成 Feature（调用方再落 store）。 */
export function draftToFeature(
  kind: 'line' | 'polygon',
  coordinates: [number, number][],
  id: string,
): SketchFeature | null {
  if (coordinates.length === 0) return null;
  if (kind === 'line') {
    if (coordinates.length < 2) return null;
    return {
      id,
      type: 'Feature',
      geometry: { type: 'LineString', coordinates },
      properties: { kind: 'sketch_line' },
    };
  }
  if (coordinates.length < 3) return null;
  return {
    id,
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [closeRing(coordinates)] },
    properties: { kind: 'sketch_polygon' },
  };
}

/** 草图要素计数（工具可用性推导用）。 */
export function countSketchVertices(features: SketchFeature[]): number {
  let n = 0;
  for (const f of features) n += geometryVertices(f.geometry).length;
  return n;
}
