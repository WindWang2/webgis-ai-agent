/**
 * Shared scale-bar math (ADR-0084, review E-3).
 *
 * live（scale-bar.tsx 此前用固定候选表取 ≤100px 的最大档）与 export
 * （export-chrome.ts 此前用 nice-number 取画布宽 12%）两套算法 —— 同一
 * zoom/画布会标出不同距离（z10、1200px：live 10km vs export 20km），正是
 * ADR-0081 要消灭的行为分叉。本模块是两侧共用的**纯函数**：nice-number
 * （1/2/5×10^k 就近）距离选择；目标像素是参数（live ~100px、导出按画布
 * 宽比例），算法本体单一。
 */

export interface NiceScale {
  /** 就距离（米）。 */
  meters: number;
  /** 该距离在当前分辨率下的像素宽（逻辑像素）。 */
  px: number;
}

/** 就近 nice 距离（1/2/5×10^k），确定性、纯函数。 */
export function computeNiceScale(
  metersPerPx: number,
  targetPx: number,
): NiceScale {
  const mpp = metersPerPx > 0 ? metersPerPx : 1;
  const target = targetPx > 0 ? targetPx : 100;
  const raw = mpp * target;
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
  let best = magnitude;
  for (const n of [1, 2, 5, 10]) {
    const candidate = n * magnitude;
    if (Math.abs(candidate - raw) < Math.abs(best - raw)) {
      best = candidate;
    }
  }
  return { meters: best, px: best / mpp };
}

/** 距离标签（1000+ 米转 km，至多 1 位小数）。 */
export function formatScaleLabel(meters: number): string {
  if (meters >= 1000) {
    return `${+((meters / 1000).toFixed(1))} km`;
  }
  return `${meters} m`;
}
