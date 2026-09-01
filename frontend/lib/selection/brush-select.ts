/**
 * Brush selection — 框选的纯函数辅助（Runtime V4 / §11，ADR-0091）。
 *
 * 语义纪律：
 * - queryRenderedFeatures 的命中集是**渲染面**事实（屏幕内已绘制要素），
 *   不是全量数据面 —— 框选永远不需要拉取/克隆 FeatureCollection；
 * - ids 有界（≤ MAX_SELECTED_IDS）：超限走谓词描述符（bbox）+ matched_count
 *   披露，不把数万 id 写进前端状态（§11 硬约束）；
 * - id 解析链与 layer-data.FEATURE_ID_KEYS 同源 —— map 框选与表格行 id
 *   共用同一稳定要素身份（map↔table 联动的接合点）。
 */

import { MAX_SELECTED_IDS } from './selection-store';

export interface BrushHit {
  /** MapLibre queryRenderedFeatures 返回的要素（只读，不克隆）。 */
  id?: number | string;
  properties?: Record<string, unknown>;
  layer?: { id?: string };
}

/** 与 lib/store/layer-data.ts 的 FEATURE_ID_KEYS 保持同一解析序。 */
export const BRUSH_ID_KEYS = ['id', 'OBJECTID', 'fid', 'osm_id', '@id', 'featureId', 'feature_id'];

/**
 * 从命中要素解析稳定 id 字段：优先 properties 中的候选键（与表格行 id
 * 同源同序），properties 全无而顶层 feature.id 在场时返回 '$id'（MapLibre
 * 的 ['id'] 表达式 —— GeoJSON 顶层 id 不在 properties 里，['get',…] 取不到）。
 * 返回 null = 无稳定 id 字段（发布侧只携带谓词与计数，不虚构过滤）。
 */
export function resolveIdField(hits: BrushHit[]): string | null {
  const sample = hits.slice(0, 5);
  for (const key of BRUSH_ID_KEYS) {
    if (sample.some((hit) => hit.properties && hit.properties[key] != null && hit.properties[key] !== '')) {
      return key;
    }
  }
  if (sample.some((hit) => hit.id != null)) return '$id';
  return null;
}

function idOf(hit: BrushHit, idField: string): string | null {
  if (idField === '$id') {
    return hit.id == null ? null : String(hit.id).slice(0, 64);
  }
  const v = hit.properties?.[idField];
  if (v == null || v === '') return null;
  return String(v).slice(0, 64);
}

export interface BrushProjection {
  /** 去重后的稳定 id（≤ MAX_SELECTED_IDS）。 */
  selected_ids: string[];
  /** 命中要素数（跨子层去重后；披露用：『已框选 N 要素』）。 */
  matched_count: number;
  /** 解析出的稳定 id 字段（null = 数据无 id 字段）。 */
  id_field: string | null;
  /** ids 是否因达到上限被截断（调用方据此附带 bbox 谓词）。 */
  truncated: boolean;
}

/** 把框选命中投影为有界选择载荷（纯函数；不触 MapLibre、不进 store）。 */
export function projectBrushHits(hits: BrushHit[]): BrushProjection {
  const idField = resolveIdField(hits);
  if (!idField) {
    return { selected_ids: [], matched_count: hits.length, id_field: null, truncated: hits.length > 0 };
  }
  // 跨子层去重（fill+outline+label 同要素多次命中只计一次）。
  const uniqueIds: string[] = [];
  const seen = new Set<string>();
  for (const hit of hits) {
    const id = idOf(hit, idField);
    if (id == null || seen.has(id)) continue;
    seen.add(id);
    uniqueIds.push(id);
  }
  // 截断 = 唯一 id 超过上限（review 修复：去重损耗不算截断）。
  const truncated = uniqueIds.length > MAX_SELECTED_IDS;
  return {
    selected_ids: uniqueIds.slice(0, MAX_SELECTED_IDS),
    matched_count: uniqueIds.length,
    id_field: idField,
    truncated,
  };
}

/** 屏幕对角两点 → 规范化屏幕矩形 {x,y,w,h}（纯几何）。 */
export function normalizeScreenRect(
  p1: { x: number; y: number },
  p2: { x: number; y: number },
): { x: number; y: number; w: number; h: number } {
  const x = Math.min(p1.x, p2.x);
  const y = Math.min(p1.y, p2.y);
  return { x, y, w: Math.abs(p1.x - p2.x), h: Math.abs(p1.y - p2.y) };
}

/** 屏幕矩形 ≥ 最小尺寸才算有效框选（误触保护）。 */
export const BRUSH_MIN_PIXELS = 6;

export function isBrushRectViable(rect: { w: number; h: number }): boolean {
  return rect.w >= BRUSH_MIN_PIXELS && rect.h >= BRUSH_MIN_PIXELS;
}
