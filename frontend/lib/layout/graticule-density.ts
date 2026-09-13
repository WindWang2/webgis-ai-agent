/**
 * graticule-density —— 经纬网密度自适应（AC-07 / ADR-0156 §P5）。
 *
 * 按视图跨度在优选间隔序列中选取使**网格线数 ∈ [3, 10]** 的间隔；
 * 用户/spec 显式指定（options.interval）时优先（explicit > adaptive）；
 * 无 bounds（首次挂载）回退既有 zoom 表（与导出侧 graticule-math 同表）。
 *
 * 纯函数、确定性；多跨度用例断言线数恒落 [3,10]（P8 验收）。
 */

import { graticuleIntervalForZoom, GRATICULE_INTERVALS } from '@/lib/map-kit/graticule-math';

export const MIN_GRID_LINES = 3;
export const MAX_GRID_LINES = 10;

/** 跨度内按 interval 吸附的网格线数（含端点线；与 snapGraticuleLines 同口径）。 */
export function gridLineCount(spanDeg: number, intervalDeg: number): number {
  if (!(spanDeg > 0) || !(intervalDeg > 0)) return 0;
  return Math.floor(spanDeg / intervalDeg) + 1;
}

export interface GraticuleDensityChoice {
  intervalDeg: number;
  source: 'explicit' | 'adaptive' | 'zoom';
  lineCountLng: number;
  lineCountLat: number;
}

/**
 * 间隔决策：explicit（用户指定）> adaptive（跨度优选序列）> zoom（既有表）。
 *
 * adaptive 是**双维联合约束**：候选间隔须同时使经度/纬度向线数 ≤ MAX；
 * 在此域内取第一个使两方向线数均 ≥ MIN 的档（粗→细扫描，优先粗网格）；
 * 若无档可同时满足 ≥MIN（极端纵横比），取「两方向线数 ≤ MAX」中
 * min(lineCount) 最大的档（保底信息量）；若连 ≤MAX 都无档（全球级跨度），
 * 取最粗档并如实出超（渲染端可再抽稀 —— 不虚构）。
 */
export function selectGraticuleInterval(
  spanLngDeg: number,
  spanLatDeg: number,
  opts?: {
    /** 用户/spec 显式指定（度）—— 合法值（>0）直接采纳。 */
    explicitIntervalDeg?: number;
    zoom?: number;
  },
): GraticuleDensityChoice {
  const explicit = opts?.explicitIntervalDeg;
  if (Number.isFinite(explicit) && (explicit as number) > 0) {
    return {
      intervalDeg: explicit as number,
      source: 'explicit',
      lineCountLng: gridLineCount(spanLngDeg, explicit as number),
      lineCountLat: gridLineCount(spanLatDeg, explicit as number),
    };
  }
  if (!(spanLngDeg > 0) || !(spanLatDeg > 0)) {
    // 无有效 bounds（首帧）→ 既有 zoom 表回退
    const interval = graticuleIntervalForZoom(opts?.zoom ?? 4);
    return {
      intervalDeg: interval, source: 'zoom',
      lineCountLng: 0, lineCountLat: 0,
    };
  }
  const counts = (interval: number) => ({
    lng: gridLineCount(spanLngDeg, interval),
    lat: gridLineCount(spanLatDeg, interval),
  });

  // Pass 1（粗→细）：首个两方向均落 [MIN, MAX] 的档
  for (const interval of GRATICULE_INTERVALS) {
    const { lng, lat } = counts(interval);
    if (lng <= MAX_GRID_LINES && lat <= MAX_GRID_LINES
      && lng >= MIN_GRID_LINES && lat >= MIN_GRID_LINES) {
      return {
        intervalDeg: interval, source: 'adaptive',
        lineCountLng: lng, lineCountLat: lat,
      };
    }
  }
  // Pass 2：两方向均 ≤ MAX 的域内，min(lineCount) 最大者（信息量保底）；
  // 平局取更粗（间隔序即粗→细，先见先得）。
  let best: GraticuleDensityChoice | null = null;
  let bestScore = -1;
  for (const interval of GRATICULE_INTERVALS) {
    const { lng, lat } = counts(interval);
    if (lng > MAX_GRID_LINES || lat > MAX_GRID_LINES) continue;
    const score = Math.min(lng, lat);
    if (score > bestScore) {
      bestScore = score;
      best = { intervalDeg: interval, source: 'adaptive', lineCountLng: lng, lineCountLat: lat };
    }
  }
  if (best) return best;
  // Pass 3：全球级跨度 —— 最粗档如实出超（披露给渲染端抽稀）
  const coarsest = GRATICULE_INTERVALS[0];
  const { lng, lat } = counts(coarsest);
  return {
    intervalDeg: coarsest, source: 'adaptive',
    lineCountLng: lng, lineCountLat: lat,
  };
}
