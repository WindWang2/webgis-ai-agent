/**
 * Shared geographic anchor projection (v2 — inset map / annotation callout).
 *
 * 注记 callout 的 anchor 坐标与插图的范围指示此前无处安放 —— live chrome
 * 是 DOM/SVG 叠加、导出是 canvas，两侧若各自投影必然漂移（ADR-0081 教训：
 * 两套解析持续分叉）。本模块是 live 与 export 共用的**纯函数**投影层：
 *
 * - `anchorFractionInBounds`：[lng, lat] → bounds 内相对位置（0–1）。
 *   线性度插值，与 graticule-math 的 live/导出两侧同语义（导出侧
 *   _drawGraticules 也是线性度换算，无第二套投影）；
 * - `boundsFromCenterZoom`：mapCenter/mapZoom + 逻辑像素尺寸 → 近似地理
 *   bounds（与 exporter._drawGraticules 完全同源的推导，供导出侧把
 *   anchor 坐标投到画布像素 —— live 侧直接拿真实 map bounds）。
 *
 * 契约：纯函数、无状态、不依赖 maplibre 实例（测试环境可跑）。
 * bounds 缺席/退化时调用方自弃（不渲染 = 不虚构位置）。
 */

export interface GeoBounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface LngLat {
  lng: number;
  lat: number;
}

export interface AnchorFraction {
  /** 经度方向比例（0=西边界，1=东边界）。 */
  fx: number;
  /** 纬度方向比例（0=南边界，1=北边界）。 */
  fy: number;
}

export function validBounds(b: GeoBounds | null | undefined): b is GeoBounds {
  return (
    !!b &&
    Number.isFinite(b.west) && Number.isFinite(b.east) &&
    Number.isFinite(b.south) && Number.isFinite(b.north) &&
    b.east > b.west && b.north > b.south
  );
}

export function validLngLat(p: LngLat | [number, number] | null | undefined): p is LngLat | [number, number] {
  if (!p) return false;
  const lng = Array.isArray(p) ? p[0] : p.lng;
  const lat = Array.isArray(p) ? p[1] : p.lat;
  return (
    Number.isFinite(lng) && Number.isFinite(lat) &&
    lng >= -180 && lng <= 180 && lat >= -90 && lat <= 90
  );
}

/** [lng, lat] → bounds 内相对位置；bounds 无效 → null（调用方自弃）。 */
export function anchorFractionInBounds(
  anchor: LngLat | [number, number],
  bounds: GeoBounds,
): AnchorFraction | null {
  const lng = Array.isArray(anchor) ? anchor[0] : anchor.lng;
  const lat = Array.isArray(anchor) ? anchor[1] : anchor.lat;
  if (!validBounds(bounds)) return null;
  return {
    fx: (lng - bounds.west) / (bounds.east - bounds.west),
    fy: (lat - bounds.south) / (bounds.north - bounds.south),
  };
}

import { metersPerPixelAt } from '@/lib/map-kit/meters-per-pixel';

/** 导出侧同源常数：meters per degree（_drawGraticules 的近似换算）。 */
export const METERS_PER_DEGREE = 111319.9;

/**
 * mapCenter/mapZoom + 逻辑像素视口 → 近似地理 bounds。
 * 与 exporter._drawGraticules 的范围推导逐项同源（metersPerPixelAt、
 * meters/degree 近似、纬度 cos 修正）—— 导出投影与导出经纬网在同一个
 * 地理坐标系里，anchor 指示位置与网格线不会互相矛盾。
 */
export function boundsFromCenterZoom(
  center: LngLat,
  zoom: number,
  widthLogicalPx: number,
  heightLogicalPx: number,
): GeoBounds | null {
  if (
    !Number.isFinite(center.lng) || !Number.isFinite(center.lat) ||
    !Number.isFinite(zoom) || widthLogicalPx <= 0 || heightLogicalPx <= 0
  ) {
    return null;
  }
  const metersPerPixel = metersPerPixelAt(zoom, center.lat);
  const halfWidthMeters = (widthLogicalPx / 2) * metersPerPixel;
  const halfHeightMeters = (heightLogicalPx / 2) * metersPerPixel;
  const halfWidthDeg = halfWidthMeters / METERS_PER_DEGREE;
  const halfHeightDeg =
    halfHeightMeters / (METERS_PER_DEGREE * Math.cos((center.lat * Math.PI) / 180));
  return {
    west: center.lng - halfWidthDeg,
    east: center.lng + halfWidthDeg,
    south: center.lat - halfHeightDeg,
    north: center.lat + halfHeightDeg,
  };
}

/** bbox [w, s, e, n] → GeoBounds（无效 → null）。 */
export function bboxToBounds(raw: unknown): GeoBounds | null {
  if (!Array.isArray(raw) || raw.length !== 4) return null;
  const w = Number(raw[0]);
  const s = Number(raw[1]);
  const e = Number(raw[2]);
  const n = Number(raw[3]);
  const bounds = { west: w, south: s, east: e, north: n };
  return validBounds(bounds) ? bounds : null;
}
