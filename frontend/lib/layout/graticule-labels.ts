/**
 * graticule-labels —— 图廓坐标注记（AC-07 / ADR-0156 §P4）。
 *
 * 格式随视图跨度自适应（制图惯例）：跨度 ≥10° 用整度、[1°,10°) 用度分、
 * <1° 用度分秒。图廓四角与边缘标注的格式决策单一源（live 渲染器与
 * 中间层 graticule.labelFormat 同源）。
 *
 * 纯函数、确定性；三档切换有断言（P8 验收）。
 */

export type GraticuleLabelFormat = 'deg' | 'dm' | 'dms';

/** 跨度 → 注记格式档。 */
export function graticuleLabelFormatForSpan(spanDeg: number): GraticuleLabelFormat {
  if (!Number.isFinite(spanDeg) || spanDeg <= 0) return 'deg';
  if (spanDeg >= 10) return 'deg';
  if (spanDeg >= 1) return 'dm';
  return 'dms';
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** 度值 → 指定格式标签（带 N/S/E/W 半球后缀；isLat 决定半球字母集）。 */
export function formatGraticuleLabel(
  value: number,
  format: GraticuleLabelFormat,
  isLat: boolean,
): string {
  if (!Number.isFinite(value)) return '';
  const hemi = isLat ? (value >= 0 ? 'N' : 'S') : (value >= 0 ? 'E' : 'W');
  const abs = Math.abs(value);
  const deg = Math.floor(abs);
  // 图廓注记等宽惯例：经度固定 3 位（008°），纬度固定 2 位（05°）
  const degLabel = String(deg).padStart(isLat ? 2 : 3, '0');
  if (format === 'deg') return `${deg}°${hemi}`;
  const minFloat = (abs - deg) * 60;
  const min = Math.floor(minFloat);
  if (format === 'dm') return `${degLabel}°${pad2(min)}′${hemi}`;
  const sec = Math.round((minFloat - min) * 60);
  const secNorm = sec === 60 ? 0 : sec;
  const minNorm = sec === 60 ? min + 1 : min;
  return `${degLabel}°${pad2(minNorm)}′${pad2(secNorm)}″${hemi}`;
}

/** 图廓注记集合：四角（经度上/下缘、纬度左/右缘）。 */
export interface FrameAnnotation {
  position: 'nw' | 'ne' | 'sw' | 'se';
  lngLabel: string;
  latLabel: string;
}

export function frameAnnotations(
  bounds: { west: number; south: number; east: number; north: number },
  format: GraticuleLabelFormat,
): FrameAnnotation[] {
  const corners: FrameAnnotation['position'][] = ['nw', 'ne', 'sw', 'se'];
  const lngByPos: Record<FrameAnnotation['position'], number> = {
    nw: bounds.west, ne: bounds.east, sw: bounds.west, se: bounds.east,
  };
  const latByPos: Record<FrameAnnotation['position'], number> = {
    nw: bounds.north, ne: bounds.north, sw: bounds.south, se: bounds.south,
  };
  return corners.map((position) => ({
    position,
    lngLabel: formatGraticuleLabel(lngByPos[position], format, false),
    latLabel: formatGraticuleLabel(latByPos[position], format, true),
  }));
}
