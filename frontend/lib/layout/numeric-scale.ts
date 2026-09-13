/**
 * numeric-scale —— 数字（比率式）比例尺 1:N（AC-07 / ADR-0156 §P3）。
 *
 * 与图形比例尺条并存（可配二选一）。N 按中心纬度的**当地真实尺度**计：
 * Web Mercator 的长度畸变系数 1/cos(φ) 已在 metersPerPixelAt 内修正
 *（metersPerPixel = C·cos(φ)/(512·2^z) 即当地米/像素），N = mpp / 像素物理尺寸。
 * 像素物理尺寸按标准 96dpi（0.264583 mm/px）计 —— 出版 DPI 由消费方
 * （08 线导出）按画幅换算后传入 `pxPhysicalMeters` 覆写。
 *
 * 纯函数、确定性；赤道/中纬/高纬误差 ≤5%（对照理论闭式，测试锁定）。
 */

import { metersPerPixelAt } from '@/lib/map-kit/meters-per-pixel';

/** 96dpi 下 1 逻辑像素的物理尺寸（米）。 */
export const PX_PHYSICAL_METERS_96DPI = 0.0254 / 96;

/**
 * 比率式比例尺：1:N（N 为当地真实尺度的分母）。
 * lat 修正在 metersPerPixelAt 内（cos φ）—— 高纬度不再谎报尺度。
 */
export function numericScaleAt(
  zoom: number,
  lat: number,
  opts?: { pxPhysicalMeters?: number },
): { ratio: number; label: string } {
  const px = opts?.pxPhysicalMeters ?? PX_PHYSICAL_METERS_96DPI;
  const mpp = metersPerPixelAt(zoom, lat);
  const raw = mpp / (px > 0 ? px : PX_PHYSICAL_METERS_96DPI);
  const rounded = effective3(raw);
  return { ratio: rounded, label: `1:${rounded.toLocaleString('en-US')}` };
}

/** 3 位有效数字取整（确定性）。 */
function effective3(x: number): number {
  if (!(x > 0) || !Number.isFinite(x)) return 1;
  const e = Math.floor(Math.log10(x));
  const scale = Math.pow(10, e - 2);
  return Math.round(x / scale) * scale;
}

/** 图形比例尺与数字比例尺的并存/二选一模式解析。 */
export function scaleDisplayMode(
  componentOptions: Record<string, unknown> | undefined,
): 'both' | 'bar' | 'numeric' {
  const mode = componentOptions?.['scaleDisplay'];
  if (mode === 'numeric' || mode === 'bar') return mode;
  return 'both'; // 默认并存（§P3）
}
