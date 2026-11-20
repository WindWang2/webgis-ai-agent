/**
 * G34 单位换算单一 corpus 的 TS 镜像。
 *
 * Python 权威：app/services/print_layout_authoring/units.py
 * Golden parity：tests/cartography/golden_corpus/print_layout_units/basic.json
 * （pytest 与 vitest 消费同一 fixture 逐字段对拍）。
 *
 * 四种单位绝不混用：毫米（持久坐标）/ pt（字号行距）/ 逻辑 px
 * （IR 与浏览器设计面，PX_PER_MM=4）/ SVG viewBox user unit（随 DPI）
 * / 采样 px（PNG 像素数量）。缩放预览只改变屏幕比例，不改毫米坐标。
 */

export const PX_PER_MM = 4.0;
export const DPI_DIVISOR = 72.0;
export const PT_PER_MM = 72.0 / 25.4;
export const MM_PER_INCH = 25.4;
export const MIN_DPI = 72.0;
export const MAX_DPI = 600.0;

export function clampDpi(dpi: unknown): number {
  const d = typeof dpi === 'number' ? dpi : Number(dpi);
  if (!Number.isFinite(d) || d <= 0) return 300.0;
  return Math.min(Math.max(d, MIN_DPI), MAX_DPI);
}

export function dpiScale(dpi: unknown): number {
  return clampDpi(dpi) / DPI_DIVISOR;
}

export function mmToLogicalPx(mm: number): number { return mm * PX_PER_MM; }
export function logicalPxToMm(px: number): number { return px / PX_PER_MM; }
export function mmToCanvasUnits(mm: number, dpi: unknown): number {
  return mm * PX_PER_MM * dpiScale(dpi);
}
export function canvasUnitsToMm(units: number, dpi: unknown): number {
  return units / (PX_PER_MM * dpiScale(dpi));
}
export function ptToMm(pt: number): number { return pt / PT_PER_MM; }
export function mmToPt(mm: number): number { return mm * PT_PER_MM; }
export function ptToCanvasUnits(pt: number, dpi: unknown): number {
  return pt * PX_PER_MM * dpiScale(dpi) / PT_PER_MM;
}
export function canvasUnitsToPt(units: number, dpi: unknown): number {
  return units * PT_PER_MM / (PX_PER_MM * dpiScale(dpi));
}
export function mmToSamplePx(mm: number, dpi: unknown): number {
  return mm * clampDpi(dpi) / MM_PER_INCH;
}
export function samplePxToMm(px: number, dpi: unknown): number {
  return px * MM_PER_INCH / clampDpi(dpi);
}

/** 与 Python units_corpus() 逐字段一致（数值保留 10 位小数）。 */
export function unitsCorpus(): Record<string, unknown> {
  const round = (v: number): number => Math.round(v * 1e10) / 1e10;
  const rows = [72, 96, 150, 300, 600].map((dpi) => ({
    dpi,
    dpi_scale: round(dpiScale(dpi)),
    mm_to_logical_px_1mm: round(mmToLogicalPx(1.0)),
    mm_to_canvas_units_1mm: round(mmToCanvasUnits(1.0, dpi)),
    canvas_units_to_mm_1unit: round(canvasUnitsToMm(1.0, dpi)),
    pt_to_canvas_units_12pt: round(ptToCanvasUnits(12.0, dpi)),
    mm_to_sample_px_1mm: round(mmToSamplePx(1.0, dpi)),
  }));
  return {
    version: 'print_layout_units.v1',
    constants: {
      px_per_mm: PX_PER_MM,
      dpi_divisor: DPI_DIVISOR,
      pt_per_mm: round(PT_PER_MM),
      mm_per_inch: MM_PER_INCH,
      min_dpi: MIN_DPI,
      max_dpi: MAX_DPI,
    },
    rows,
  };
}

/** 纸面预览缩放：只改变屏幕显示比例，持久毫米坐标不变。 */
export function previewScale(zoom: number): number {
  const z = Number.isFinite(zoom) ? zoom : 1;
  return Math.min(4, Math.max(0.25, z));
}
