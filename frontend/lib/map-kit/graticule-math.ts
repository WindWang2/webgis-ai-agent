/**
 * Shared graticule geometry (P3, #1089 deferred — live graticule renderer).
 *
 * 经纬网的间隔表、zoom→间隔映射与网格吸附数学此前只存在于 exporter 的
 * `_drawGraticules`（导出侧）。live 渲染器落地后两侧共用本模块 —— 单一
 * 语义源（ADR-0081 parity：live 与 export 不发明第二套间隔/吸附规则）。
 *
 * 纯函数、无状态、O(lines)。
 */

/** 导出侧同表：zoom 越高间隔越小（每 2 级 zoom 换档）。 */
export const GRATICULE_INTERVALS = [30, 20, 10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01];

/** zoom → 经纬网间隔（与导出侧同映射：floor((zoom-1)/2) 夹取到表内）。 */
export function graticuleIntervalForZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return GRATICULE_INTERVALS[GRATICULE_INTERVALS.length - 1];
  const index = Math.max(0, Math.min(Math.floor((zoom - 1) / 2), GRATICULE_INTERVALS.length - 1));
  return GRATICULE_INTERVALS[index];
}

/** [min, max] 度范围内按 interval 吸附生成的网格线值（升序）。 */
export function snapGraticuleLines(minDeg: number, maxDeg: number, interval: number): number[] {
  if (!Number.isFinite(minDeg) || !Number.isFinite(maxDeg) || !(interval > 0)) return [];
  const start = Math.floor(minDeg / interval) * interval;
  const lines: number[] = [];
  // +epsilon 防浮点漂移吞掉最后一条线；行数有界（范围/间隔 ≤ 512 才生成，
  // 防御退化输入）
  const maxLines = 512;
  for (let v = start, n = 0; v <= maxDeg + interval * 1e-9 && n < maxLines; v += interval, n += 1) {
    lines.push(Number(v.toFixed(6)));
  }
  return lines;
}

export interface GraticuleLine {
  /** 度值（吸附后）。 */
  value: number;
  /** 视口内的相对位置（0–1；lng 线为水平比例，lat 线为自底部比例）。 */
  fraction: number;
  /** 度标签（与导出侧同格式：绝对值 + N/S/E/W，interval<1 保留 1 位小数）。 */
  label: string;
}

/** 经度线（竖直线）集合。 */
export function graticuleLngLines(
  minLng: number,
  maxLng: number,
  interval: number,
): GraticuleLine[] {
  return snapGraticuleLines(minLng, maxLng, interval).map((lng) => ({
    value: lng,
    fraction:
      maxLng > minLng ? (lng - minLng) / (maxLng - minLng) : 0,
    label: `${Math.abs(lng).toFixed(interval < 1 ? 1 : 0)}°${lng >= 0 ? 'E' : 'W'}`,
  }));
}

/** 纬度线（水平线）集合（fraction 自底部起算，与导出 y 翻转一致）。 */
export function graticuleLatLines(
  minLat: number,
  maxLat: number,
  interval: number,
): GraticuleLine[] {
  return snapGraticuleLines(minLat, maxLat, interval).map((lat) => ({
    value: lat,
    fraction:
      maxLat > minLat ? (lat - minLat) / (maxLat - minLat) : 0,
    label: `${Math.abs(lat).toFixed(interval < 1 ? 1 : 0)}°${lat >= 0 ? 'N' : 'S'}`,
  }));
}
