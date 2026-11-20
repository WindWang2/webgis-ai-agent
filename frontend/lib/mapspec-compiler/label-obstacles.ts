/**
 * 带身份的标注图元障碍抽取（G16 geometry_v1）—— app/lib/cartography/
 * label_obstacles.py 的 1:1 TS 孪生。语义/上限/近似口径逐常量一致
 * （线逐段 AABB + 实际 stroke 宽；面外环边界；禁入面自适应网格 + 偶奇
 * 判定 + 放大保铺满；组件矩形 ×dpi_scale；分组公平交错预算）。
 */
import type { Box } from '@/lib/label-geometry';
import {
  MAX_OBSTACLES_PER_SOLVE,
  type ObstacleBox,
} from './label-solver';

export type { ObstacleBox };

export const MAX_SEGMENTS_PER_FEATURE = 64;
export const MAX_SEGMENT_OBSTACLES_PER_LAYER = 320;
export const ZONE_GRID_TARGET_RANK = 20;
export const ZONE_GRID_MIN_CELL_PX = 16.0;
export const ZONE_GRID_MAX_CELL_PX = 64.0;
export const MAX_ZONE_GRID_CELLS = 96;
export const COMPONENT_OBSTACLE_TYPES = [
  'legend', 'continuous_colorbar', 'categorical_legend',
  'north_arrow', 'scale_bar', 'title', 'subtitle',
] as const;
export const MAX_COMPONENT_OBSTACLES = 16;

export type ProjectFn = (pt: readonly number[]) => [number, number] | [string, string];
export type StrokeWidthFn = (props: Record<string, unknown>) => number;

export interface FeatureLike {
  geometry?: { type?: string; coordinates?: unknown } | null;
  properties?: Record<string, unknown> | null;
  id?: string | number;
}

export function projectBoxIntersectsViewport(
  box: Box, viewport: readonly number[],
): boolean {
  return !(
    box[2] <= viewport[0] || box[0] >= viewport[2]
    || box[3] <= viewport[1] || box[1] >= viewport[3]
  );
}

const FEATURE_ID_KEYS = [
  'id', 'OBJECTID', 'fid', 'FID', 'osm_id', '@id', 'featureId', 'feature_id',
] as const;

/** 单要素身份（与 backend label_authoring.resolve_feature_identity 同源）。 */
export function resolveFeatureIdentity(
  feature: FeatureLike,
): { featureId: string | null; idKey: string | null } {
  const top = (feature as { id?: unknown }).id;
  if (typeof top === 'string' || typeof top === 'number') {
    return { featureId: String(top), idKey: null };
  }
  const props = feature.properties;
  if (props && typeof props === 'object') {
    for (const key of FEATURE_ID_KEYS) {
      const v = props[key];
      if (typeof v === 'string' || typeof v === 'number') {
        return { featureId: String(v), idKey: key };
      }
    }
  }
  return { featureId: null, idKey: null };
}

function isPt(c: unknown): c is number[] {
  return (
    Array.isArray(c) && c.length >= 2
    && typeof c[0] === 'number' && typeof c[1] === 'number'
  );
}

function iterRings(coords: unknown): number[][][] {
  const out: number[][][] = [];
  if (!Array.isArray(coords)) return out;
  if (coords.length > 0 && isPt(coords[0])) {
    out.push(coords as number[][]);
    return out;
  }
  for (const sub of coords) {
    if (!Array.isArray(sub)) continue;
    if (sub.length > 0 && isPt(sub[0])) {
      out.push(sub as number[][]);
    } else {
      for (const ring of sub) {
        if (Array.isArray(ring) && ring.length > 0 && isPt(ring[0])) {
          out.push(ring as number[][]);
        }
      }
    }
  }
  return out;
}

function segmentBoxes(
  coords: unknown,
  opts: {
    layerId: string;
    featureId: string | null;
    kind: 'line' | 'polygon_boundary';
    project: ProjectFn;
    strokePx: number;
    viewport: readonly number[];
    perFeatureCap: number;
    budgetLeft: number;
  },
): { obstacles: ObstacleBox[]; used: number } {
  const obstacles: ObstacleBox[] = [];
  let used = 0;
  const half = Math.abs(opts.strokePx) / 2 + 0.5;
  for (const ring of iterRings(coords)) {
    const n = ring.length;
    if (n < 2 || used >= opts.perFeatureCap || opts.budgetLeft - used <= 0) break;
    const stride = Math.max(1, Math.ceil((n - 1) / opts.perFeatureCap));
    let i = 0;
    while (i < n - 1 && used < opts.perFeatureCap && used < opts.budgetLeft) {
      const j = Math.min(i + stride, n - 1);
      const [axRaw, ayRaw] = opts.project(ring[i]);
      const [bxRaw, byRaw] = opts.project(ring[j]);
      const ax = Number(axRaw), ay = Number(ayRaw);
      const bx = Number(bxRaw), by = Number(byRaw);
      if (![ax, ay, bx, by].every(Number.isFinite)) {
        i = j;
        continue;
      }
      const x1 = Math.min(ax, bx), x2 = Math.max(ax, bx);
      const y1 = Math.min(ay, by), y2 = Math.max(ay, by);
      const box: Box = [x1 - half, y1 - half, x2 + half, y2 + half];
      if (projectBoxIntersectsViewport(box, opts.viewport)) {
        obstacles.push({
          box, kind: opts.kind, layerId: opts.layerId,
          featureId: opts.featureId ?? undefined,
        });
        used += 1;
      }
      i = j;
    }
  }
  return { obstacles, used };
}

export function extractLineObstacles(
  features: readonly FeatureLike[],
  opts: {
    layerId: string;
    project: ProjectFn;
    strokeWidth: StrokeWidthFn;
    viewport: readonly number[];
    budget?: number;
  },
): ObstacleBox[] {
  const out: ObstacleBox[] = [];
  let remaining = opts.budget ?? MAX_SEGMENT_OBSTACLES_PER_LAYER;
  for (const feat of features) {
    if (remaining <= 0) break;
    const geom = feat.geometry;
    if (!geom || (geom.type !== 'LineString' && geom.type !== 'MultiLineString')) continue;
    const { featureId } = resolveFeatureIdentity(feat);
    const { obstacles } = segmentBoxes(geom.coordinates, {
      layerId: opts.layerId, featureId, kind: 'line',
      project: opts.project,
      strokePx: opts.strokeWidth(feat.properties ?? {}),
      viewport: opts.viewport,
      perFeatureCap: MAX_SEGMENTS_PER_FEATURE, budgetLeft: remaining,
    });
    out.push(...obstacles);
    remaining -= obstacles.length;
  }
  return out;
}

export function extractPolygonBoundaryObstacles(
  features: readonly FeatureLike[],
  opts: {
    layerId: string;
    project: ProjectFn;
    strokeWidth: StrokeWidthFn;
    viewport: readonly number[];
    budget?: number;
  },
): ObstacleBox[] {
  const out: ObstacleBox[] = [];
  let remaining = opts.budget ?? MAX_SEGMENT_OBSTACLES_PER_LAYER;
  for (const feat of features) {
    if (remaining <= 0) break;
    const geom = feat.geometry;
    if (!geom || (geom.type !== 'Polygon' && geom.type !== 'MultiPolygon')) continue;
    const { featureId } = resolveFeatureIdentity(feat);
    const rings = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
    const { obstacles } = segmentBoxes(rings, {
      layerId: opts.layerId, featureId, kind: 'polygon_boundary',
      project: opts.project,
      strokePx: opts.strokeWidth(feat.properties ?? {}),
      viewport: opts.viewport,
      perFeatureCap: MAX_SEGMENTS_PER_FEATURE, budgetLeft: remaining,
    });
    out.push(...obstacles);
    remaining -= obstacles.length;
  }
  return out;
}

function pointInRing(x: number, y: number, ring: readonly (readonly number[])[]): boolean {
  let inside = false;
  const n = ring.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    if ((yi > y) !== (yj > y)) {
      const xCross = ((xj - xi) * (y - yi)) / (yj - yi) + xi;
      if (x < xCross) inside = !inside;
    }
  }
  return inside;
}

export interface ForbiddenZoneLike {
  id?: unknown;
  ring?: unknown;
}

export function extractZoneObstacles(
  zones: readonly ForbiddenZoneLike[],
  opts: { project: ProjectFn; viewport: readonly number[] },
): ObstacleBox[] {
  const out: ObstacleBox[] = [];
  for (const zone of zones) {
    const zid = zone?.id;
    const ring = zone?.ring;
    if (typeof zid !== 'string' || !Array.isArray(ring) || ring.length < 4) continue;
    const pts: Array<[number, number]> = [];
    let ringValid = true;
    for (const p of ring) {
      if (!Array.isArray(p) || p.length < 2) { ringValid = false; break; }
      const [pxRaw, pyRaw] = opts.project(p as number[]);
      const px = Number(pxRaw), py = Number(pyRaw);
      if (Number.isFinite(px) && Number.isFinite(py)) {
        pts.push([px, py]);
      } else {
        // Python 孪生口径：任一点非法 → 弃整个 zone（部分环 = 错误
        // 多边形，比缺角环诚实）。
        ringValid = false;
        break;
      }
    }
    if (!ringValid || pts.length < 4) continue;
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    if (maxX <= minX || maxY <= minY) continue;
    const longSide = Math.max(maxX - minX, maxY - minY);
    let cell = Math.min(
      Math.max(longSide / ZONE_GRID_TARGET_RANK, ZONE_GRID_MIN_CELL_PX),
      ZONE_GRID_MAX_CELL_PX,
    );
    const decompose = (cellPx: number): ObstacleBox[] => {
      const stepX = (maxX - minX) / Math.max(1, Math.ceil((maxX - minX) / cellPx));
      const stepY = (maxY - minY) / Math.max(1, Math.ceil((maxY - minY) / cellPx));
      const rankX = Math.max(1, Math.ceil((maxX - minX) / stepX));
      const rankY = Math.max(1, Math.ceil((maxY - minY) / stepY));
      const cells: ObstacleBox[] = [];
      for (let gy = 0; gy < rankY; gy++) {
        for (let gx = 0; gx < rankX; gx++) {
          const cx = minX + (gx + 0.5) * stepX;
          const cy = minY + (gy + 0.5) * stepY;
          if (!pointInRing(cx, cy, pts)) continue;
          const box: Box = [
            cx - stepX / 2, cy - stepY / 2, cx + stepX / 2, cy + stepY / 2,
          ];
          if (projectBoxIntersectsViewport(box, opts.viewport)) {
            cells.push({ box, kind: 'zone', featureId: `zone:${zid}` });
          }
        }
      }
      return cells;
    };
    let cells = decompose(cell);
    let grown = 0;
    while (cells.length > MAX_ZONE_GRID_CELLS && grown < 4) {
      cell *= 2;
      cells = decompose(cell);
      grown += 1;
    }
    if (cells.length > MAX_ZONE_GRID_CELLS) {
      const stride = Math.ceil(cells.length / MAX_ZONE_GRID_CELLS);
      cells = cells.filter((_, i) => i % stride === 0);
    }
    out.push(...cells);
  }
  return out;
}

export interface ComponentLike {
  id?: unknown;
  type?: unknown;
  placement?: Record<string, unknown> | null;
}

export function extractComponentObstacles(
  components: readonly ComponentLike[],
  opts: { viewport: readonly number[]; scale?: number },
): ObstacleBox[] {
  const scale = opts.scale ?? 1;
  const out: ObstacleBox[] = [];
  for (const comp of components) {
    if (out.length >= MAX_COMPONENT_OBSTACLES) break;
    const ctype = comp?.type;
    if (typeof ctype !== 'string'
        || !(COMPONENT_OBSTACLE_TYPES as readonly string[]).includes(ctype)) continue;
    const placement = comp?.placement;
    if (!placement || placement.mode !== 'floating') continue;
    const x = Number(placement.x) * scale;
    const y = Number(placement.y) * scale;
    const w = Number(placement.width) * scale;
    const h = Number(placement.height) * scale;
    if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) continue;
    const box: Box = [x, y, x + w, y + h];
    if (projectBoxIntersectsViewport(box, opts.viewport)) {
      out.push({
        box, kind: 'component',
        featureId: typeof comp.id === 'string' ? `component:${comp.id}` : undefined,
      });
    }
  }
  return out;
}

/** 分组公平交错到总预算（组序即优先序；截断数如实返回）。 */
export function interleaveBudgeted(
  groups: readonly (readonly ObstacleBox[])[],
  totalCap: number = MAX_OBSTACLES_PER_SOLVE,
): { obstacles: ObstacleBox[]; truncated: number } {
  const queues = groups.filter((g) => g.length > 0).map((g) => [...g]);
  const out: ObstacleBox[] = [];
  const idx = queues.map(() => 0);
  const total = queues.reduce((n, q) => n + q.length, 0);
  for (;;) {
    if (out.length >= totalCap) break;
    let progressed = false;
    for (let qi = 0; qi < queues.length; qi++) {
      if (out.length >= totalCap) break;
      if (idx[qi] < queues[qi].length) {
        out.push(queues[qi][idx[qi]]);
        idx[qi] += 1;
        progressed = true;
      }
    }
    if (!progressed) break;
  }
  return { obstacles: out, truncated: Math.max(0, total - out.length) };
}
