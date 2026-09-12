/**
 * Raster canvas — cube 窗口数组的客户端渲染纯函数（ADR-0141 上图选型）。
 *
 * 后端"不推图片"（README 贡献红线：No Raster Push —— 交付源数据，渲染交给
 * 前端）。cube window / labeled window 返回嵌套 number 数组，本模块负责：
 *   1. 统计摘要（min/max/mean，nodata 剔除）；
 *   2. nodata 掩膜（逐变量 nodata，V8 cube v3 契约）；
 *   3. 线性拉伸 + 定点色带（colorbar）映射；
 *   4. canvas → data URL，封装为 HeatmapRasterSource 走既有 raster 通道。
 *
 * 纯函数与 DOM 隔离：colormap/stats/mask 可在 node 测试；dataUrl 系列仅在浏览器。
 */
import type { HeatmapRasterSource } from '@/lib/types';

/** 定点色带（与 continuous-legend 展示族同源的可视 ramp；[r,g,b] 0-255）。 */
export type RGB = readonly [number, number, number];

/** 经典 viridis 抽样（6 停靠点，低——高）。 */
export const COLOR_RAMP: readonly RGB[] = [
  [68, 1, 84],
  [59, 82, 139],
  [33, 145, 140],
  [94, 201, 98],
  [253, 231, 37],
  [255, 255, 191],
];

export type ColorRampName = 'viridis' | 'inferno' | 'grayscale';

const RAMPS: Record<ColorRampName, readonly RGB[]> = {
  viridis: COLOR_RAMP,
  inferno: [
    [0, 0, 4],
    [87, 16, 110],
    [188, 55, 84],
    [249, 142, 9],
    [252, 255, 164],
  ],
  grayscale: [
    [0, 0, 0],
    [255, 255, 255],
  ],
};

/** 0-1 → 色带插值（末端钳制；两个停靠点间线性混合）。 */
export function rampColor(t: number, ramp: ColorRampName = 'viridis'): RGB {
  const stops = RAMPS[ramp];
  const clamped = Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0;
  const scaled = clamped * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(scaled));
  const f = scaled - i;
  const a = stops[i];
  const b = stops[i + 1];
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f),
  ];
}

export interface BandStats {
  min: number;
  max: number;
  mean: number;
  /** 掩膜后的有效像元数。 */
  validCount: number;
  /** 掩膜掉的像元数（nodata / 非有限值）。 */
  maskedCount: number;
}

/** nodata 判定：显式 nodata 相等（± 容差）或非有限值一律掩膜。 */
function isNoData(v: number, nodata: number | null | undefined): boolean {
  if (!Number.isFinite(v)) return true;
  if (nodata === null || nodata === undefined) return false;
  return v === nodata;
}

/** 二维网格统计（nodata 剔除后）。 */
export function bandStats(
  grid: number[][],
  nodata?: number | null,
): BandStats {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  let sum = 0;
  let validCount = 0;
  let maskedCount = 0;
  for (const row of grid) {
    for (const v of row) {
      if (isNoData(v, nodata)) {
        maskedCount += 1;
        continue;
      }
      if (v < min) min = v;
      if (v > max) max = v;
      sum += v;
      validCount += 1;
    }
  }
  if (validCount === 0) return { min: 0, max: 0, mean: 0, validCount: 0, maskedCount };
  return { min, max, mean: sum / validCount, validCount, maskedCount };
}

/** 直方图（bins 等宽；nodata 剔除）—— 统计摘要图用。 */
export function bandHistogram(
  grid: number[][],
  bins: number,
  nodata?: number | null,
  range?: { min: number; max: number },
): Array<{ name: string; value: number }> {
  const r = range ?? bandStats(grid, nodata);
  const width = (r.max - r.min) / bins || 1;
  const counts = new Array<number>(bins).fill(0);
  for (const row of grid) {
    for (const v of row) {
      if (isNoData(v, nodata)) continue;
      const idx = Math.min(bins - 1, Math.max(0, Math.floor((v - r.min) / width)));
      counts[idx] += 1;
    }
  }
  return counts.map((value, i) => ({
    name: (r.min + (i + 0.5) * width).toPrecision(3),
    value,
  }));
}

export interface RenderGridOptions {
  nodata?: number | null;
  /** 拉伸域；缺省用 bandStats（min-max 线性拉伸）。 */
  domain?: { min: number; max: number };
  ramp?: ColorRampName;
  /** nodata 像元 alpha（0 = 完全透明掩膜）。 */
  noDataAlpha?: number;
}

/**
 * 网格 → RGBA 像素（行序 = grid 行序；nodata → 透明掩膜）。
 * 返回 Uint8ClampedArray 与尺寸，调用方 putImageData / new ImageData。
 */
export function gridToRgba(
  grid: number[][],
  opts: RenderGridOptions = {},
): { pixels: Uint8ClampedArray; width: number; height: number } {
  const height = grid.length;
  const width = height > 0 ? grid[0].length : 0;
  const pixels = new Uint8ClampedArray(width * height * 4);
  const domain = opts.domain ?? bandStats(grid, opts.nodata);
  const span = domain.max - domain.min || 1;
  const noDataAlpha = opts.noDataAlpha ?? 0;
  for (let y = 0; y < height; y += 1) {
    const row = grid[y];
    for (let x = 0; x < width; x += 1) {
      const v = row[x];
      const o = (y * width + x) * 4;
      if (isNoData(v, opts.nodata)) {
        pixels[o] = 0;
        pixels[o + 1] = 0;
        pixels[o + 2] = 0;
        pixels[o + 3] = noDataAlpha;
        continue;
      }
      const [r, g, b] = rampColor((v - domain.min) / span, opts.ramp ?? 'viridis');
      pixels[o] = r;
      pixels[o + 1] = g;
      pixels[o + 2] = b;
      pixels[o + 3] = 255;
    }
  }
  return { pixels, width, height };
}

/**
 * 网格 → PNG data URL（浏览器 only；jsdom 无 canvas 2d —— 测试用注入桩或
 * 跳过）。长边缩放到 maxSide 内避免 4k 窗口生成超大位图。
 */
export function gridToDataUrl(
  grid: number[][],
  opts: RenderGridOptions & { maxSide?: number } = {},
): string | null {
  if (typeof document === 'undefined') return null;
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  const { pixels, width, height } = gridToRgba(grid, opts);
  canvas.width = Math.max(1, width);
  canvas.height = Math.max(1, height);
  const image = ctx.createImageData(width, height);
  image.data.set(pixels);
  ctx.putImageData(image, 0, 0);
  const maxSide = opts.maxSide ?? 2048;
  const scale = Math.min(1, maxSide / Math.max(canvas.width, canvas.height));
  if (scale < 1) {
    const out = document.createElement('canvas');
    out.width = Math.max(1, Math.round(canvas.width * scale));
    out.height = Math.max(1, Math.round(canvas.height * scale));
    const octx = out.getContext('2d');
    if (!octx) return canvas.toDataURL('image/png');
    octx.imageSmoothingEnabled = false;
    octx.drawImage(canvas, 0, 0, out.width, out.height);
    return out.toDataURL('image/png');
  }
  return canvas.toDataURL('image/png');
}

/**
 * 窗口数组 → HeatmapRasterSource（既有 raster 通道：adapter isHeatmapRasterSource
 * → maplibre raster source）。grid 为单帧 [y][x]；bbox = 地理范围
 * [minx, miny, maxx, maxy]。
 */
export function gridToRasterSource(
  grid: number[][],
  bbox: [number, number, number, number],
  opts: RenderGridOptions = {},
): HeatmapRasterSource | null {
  const image = gridToDataUrl(grid, opts);
  if (!image) return null;
  return { image, bbox };
}
