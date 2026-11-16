/**
 * G47 图面几何：等比 contain、命中测试、键盘顺序（纯函数，可单测）。
 *
 * 铁律：**只允许统一缩放**。任何把 x/y 用不同系数拉伸的做法都会把圆变成椭圆，
 * 从而破坏"面积∝数量"的读图基础 —— 这里不提供非等比 fit。
 */

export interface Box {
  width: number;
  height: number;
}

export interface ContainFit {
  scale: number;
  offsetX: number;
  offsetY: number;
}

/** 把 extent×extent 的示意图等比装进任意长宽比的容器（居中留白）。 */
export function fitContain(extent: number, box: Box): ContainFit {
  const e = Number.isFinite(extent) && extent > 0 ? extent : 1000;
  const w = Number.isFinite(box.width) && box.width > 0 ? box.width : e;
  const h = Number.isFinite(box.height) && box.height > 0 ? box.height : e;
  const scale = Math.min(w / e, h / e);
  return {
    scale,
    offsetX: (w - e * scale) / 2,
    offsetY: (h - e * scale) / 2,
  };
}

/** 容器像素坐标 → 示意坐标（用于命中测试；不做任何非等比变换）。 */
export function toDiagramPoint(
  px: number, py: number, extent: number, box: Box,
): { x: number; y: number } {
  const fit = fitContain(extent, box);
  const s = fit.scale || 1;
  return { x: (px - fit.offsetX) / s, y: (py - fit.offsetY) / s };
}

export interface HitCircle {
  feature_id: string;
  x: number;
  y: number;
  radius: number;
}

/** 命中最近的真实圆（重叠时取半径更小的那个，符合"小圆在上"的直觉）。 */
export function hitTest(
  circles: HitCircle[], x: number, y: number,
): string | null {
  let best: { id: string; r: number } | null = null;
  for (const c of circles) {
    const dx = x - c.x;
    const dy = y - c.y;
    if (dx * dx + dy * dy > c.radius * c.radius) continue;
    if (!best || c.radius < best.r) best = { id: c.feature_id, r: c.radius };
  }
  return best ? best.id : null;
}

/** 键盘导航：按稳定 id 顺序循环（不按屏幕距离，不依赖布局坐标）。 */
export function nextFeatureId(
  ids: string[], current: string | null, delta: number,
): string | null {
  if (!ids.length) return null;
  const ordered = [...ids].sort();
  if (!current) return ordered[0];
  const i = ordered.indexOf(current);
  if (i < 0) return ordered[0];
  const n = ordered.length;
  return ordered[((i + delta) % n + n) % n];
}

/** 面积比自检（前端只展示，不替代服务端独立 oracle）。 */
export function areaRatioError(
  circles: Array<{ value: number; radius: number }>,
): number {
  const positives = circles.filter((c) => c.value > 0 && c.radius > 0);
  if (positives.length < 2) return 0;
  const k = (positives[0].radius ** 2) / positives[0].value;
  let worst = 0;
  for (const c of positives) {
    const expected = k * c.value;
    const actual = c.radius ** 2;
    worst = Math.max(worst, Math.abs(actual - expected) / expected);
  }
  return worst;
}

/** SVG path（原地理参照轮廓）。 */
export function ringPath(ring: Array<[number, number] | number[]>): string {
  const pts = ring.filter((p) => Array.isArray(p) && p.length >= 2);
  if (pts.length < 3) return '';
  const [first, ...rest] = pts as number[][];
  const head = `M ${first[0]} ${first[1]}`;
  return `${head} ${rest.map((p) => `L ${p[0]} ${p[1]}`).join(' ')} Z`;
}
