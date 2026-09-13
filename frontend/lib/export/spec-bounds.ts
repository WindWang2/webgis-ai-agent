/**
 * ac-08（ADR-0157 P4）· MapSpec 数据范围推断（超界提示用）。
 *
 * 坐标提取语义与 mapspec-to-svg 的 auto-extent 一致（inlineData/data 的
 * FeatureCollection/Feature 递归坐标），但**不带 world 兜底**：无有效数据
 * → null（调用方视为「无数据范围依据」，不发超界提示 —— 兜底范围是编译器
 * 的出图策略，不是数据事实）。
 *
 * 独立实现而非复用编译器内部逻辑：编译器输出受 TS/Python 字节 parity
 * 金样锁定，export 侧只读语义不回写，避免牵动 parity 面。
 */
import type { BoundsWSEN } from './extent';

/** 从 MapSpec 的 inline 数据源推断数据经纬度范围。无有效坐标 → null。 */
export function inferSpecDataBounds(spec: unknown): BoundsWSEN | null {
  const sources = (spec as { sources?: Record<string, unknown> })?.sources;
  if (!sources || typeof sources !== 'object') return null;

  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;

  const extractCoords = (c: unknown): void => {
    if (
      Array.isArray(c) &&
      c.length >= 2 &&
      typeof c[0] === 'number' &&
      typeof c[1] === 'number' &&
      Number.isFinite(c[0]) &&
      Number.isFinite(c[1])
    ) {
      if (c[0] < minX) minX = c[0];
      if (c[0] > maxX) maxX = c[0];
      if (c[1] < minY) minY = c[1];
      if (c[1] > maxY) maxY = c[1];
    } else if (Array.isArray(c)) {
      c.forEach(extractCoords);
    }
  };

  for (const src of Object.values(sources)) {
    const geojson = (src as { inlineData?: unknown; data?: unknown })?.inlineData ??
      (src as { data?: unknown })?.data;
    if (!geojson || typeof geojson !== 'object') continue;
    const features =
      (geojson as { type?: string }).type === 'FeatureCollection'
        ? (geojson as { features?: unknown[] }).features ?? []
        : [geojson];
    for (const feat of features) {
      const geom = (feat as { geometry?: { coordinates?: unknown } })?.geometry;
      if (!geom) continue;
      extractCoords(geom.coordinates);
    }
  }

  if (![minX, maxX, minY, maxY].every(Number.isFinite) || maxX < minX || maxY < minY) {
    return null;
  }
  return [minX, minY, maxX, maxY];
}
