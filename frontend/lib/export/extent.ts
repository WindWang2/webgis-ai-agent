/**
 * ac-08（ADR-0157 P4）· 所见即所得出图范围数学。
 *
 * 痛点（recon §1.1 / §2-#3）：现状 canvas 导出只对 live 视口做 1.414 中心
 * 裁切 —— 出图范围与用户看到的遮罩范围无契约，边缘内容静默丢失。
 *
 * 契约：
 * - 导出范围（export bounds）⊇ 遮罩范围（mask bounds，即 live 视口所见），
 *   且纵横比（Web Mercator 空间）与纸张图框一致 → fitBounds 后视口中心
 *   裁切恰好等于导出范围，遮罩内容零裁切；
 * - 遮罩范围在导出件内保持其相对位置（fitBounds 居中 + 等比贴合，几何误差
 *   由 ≤1px 探针验收，见 scripts/ac08/dpi-line-probe.mjs 同族探针）；
 * - 数据范围超出导出范围 → 调用方发 `extent_overflow_data` 提示（不静默
 *   裁切内容：超出部分以画布背景呈现，§0.5 契约）。
 *
 * 全部纯函数（无 maplibre / DOM 依赖），TS/Python 双端镜像可对拍。
 */

/** [west, south, east, north]，度。 */
export type BoundsWSEN = [number, number, number, number];

/** Web Mercator 归一 y（0=赤道，1=纬度 85.05°）；无效纬度钳到投影范围。 */
export function mercNormY(lat: number): number {
  const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat));
  return 0.5 - Math.log(Math.tan(Math.PI / 4 + (clamped * Math.PI) / 360)) / (2 * Math.PI);
}

function isFiniteBounds(b: BoundsWSEN): boolean {
  return (
    b.length === 4 &&
    b.every(Number.isFinite) &&
    b[2] > b[0] && // east > west
    b[3] > b[1] // north > south
  );
}

/**
 * 遮罩范围 → 纸张图框导出范围：以 Mercator 中心为不动点，把短边扩张到
 * 图框纵横比（width/height）。遮罩永远完整保留；aspectWH ≤ 0 或遮罩非法
 * → null（调用方退回现状裁切语义并如实披露）。
 */
export function exportBoundsForFrame(
  mask: BoundsWSEN,
  aspectWH: number,
): BoundsWSEN | null {
  if (!isFiniteBounds(mask) || !(aspectWH > 0)) return null;
  const [w, s, e, n] = mask;
  const y0 = mercNormY(s);
  const y1 = mercNormY(n);
  // 经度与纬度必须同一量纲（归一化）：x ∈ [0,1] 对应 360°，y ∈ [0,1]
  // 对应 Mercator 全程。此前经度直接用度数 → 纵横比分支错判（测试捕获）。
  const mercW = (e - w) / 360;
  const mercH = y0 - y1; // 北为 y 小方向
  if (!(mercW > 0) || !(mercH > 0)) return null;

  const cx = (w + e) / 2;
  const cy = (y0 + y1) / 2;
  const currentAspect = mercW / mercH;

  let outW: number;
  let outH: number;
  if (currentAspect > aspectWH) {
    // 遮罩偏宽 → 南北扩张
    outW = mercW;
    outH = mercW / aspectWH;
  } else {
    // 遮罩偏高 → 东西扩张
    outH = mercH;
    outW = mercH * aspectWH;
  }

  const halfW = outW / 2;
  const west = cx - halfW * 360;
  const east = cx + halfW * 360;
  // Mercator y → 纬度（标准逆变换）；钳到投影边界（极近极区的遮罩只保证
  // 包含性，不保证中心对称）。
  const latOf = (y: number): number => {
    const clamped = Math.max(0, Math.min(1, y));
    const mercN = 2 * Math.PI * (0.5 - clamped);
    return (2 * Math.atan(Math.exp(mercN)) - Math.PI / 2) * (180 / Math.PI);
  };
  const south = latOf(cy + outH / 2);
  const north = latOf(cy - outH / 2);
  return [west, south, east, north];
}

/** 数据范围是否完全落入导出范围（含边界接触；任一非法 → true 即不提示）。 */
export function boundsContained(
  data: BoundsWSEN | null | undefined,
  frame: BoundsWSEN | null | undefined,
): boolean {
  if (!data || !frame || !isFiniteBounds(data) || !isFiniteBounds(frame)) return true;
  const eps = 1e-9;
  return (
    data[0] >= frame[0] - eps &&
    data[1] >= frame[1] - eps &&
    data[2] <= frame[2] + eps &&
    data[3] <= frame[3] + eps
  );
}

/** 遮罩范围按导出范围归一后的像素位置（≤1px 验收的解析基准）。 */
export function projectLngLatToPixel(
  lng: number,
  lat: number,
  frame: BoundsWSEN,
  pixelWidth: number,
  pixelHeight: number,
): { x: number; y: number } {
  if (!isFiniteBounds(frame)) throw new Error('projectLngLatToPixel: invalid frame bounds');
  const [w, s, e, n] = frame;
  const x0 = mercNormY(n);
  const x1 = mercNormY(s);
  const fx = ((lng - w) / (e - w)) * pixelWidth;
  const fy = ((mercNormY(lat) - x0) / (x1 - x0)) * pixelHeight;
  return { x: fx, y: fy };
}
