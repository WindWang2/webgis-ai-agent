/**
 * Raster Dynamic Stretch —— 栅格拉伸客户端化（V11 W3.3，ADR-0163）。
 *
 * 服务端只下发「量化数据 + 拉伸参数」（app/services/raster_stretch.py 为
 * 权威实现）；本模块用同一算法在客户端实时着色 —— 换色带/改拉伸零请求。
 * 烘焙 PNG（render_array_to_png）保留为导出/离线兜底；双路径 parity 由
 * 共享 fixture 对拍锁定。
 *
 * 量化契约（与服务端逐位一致）：uint8 量化档（0..254 = 有效，255 = nodata
 * 保留档）+ nodata 位图；色带 stops 线性插值；确定性纯函数。
 */

export const STRETCH_PAYLOAD_VERSION = 1;

/** nodata 保留量化档（与服务端 QUANT_NODATA 同值）。 */
export const QUANT_NODATA = 255;

export interface RasterStretchPayload {
  version: number;
  mode: 'dynamic_stretch';
  width: number;
  height: number;
  palette: string;
  paletteHex: string[];
  stretch: {
    clipLo: number;
    clipHi: number;
    clipPercentiles: [number, number];
  };
  /** uint8 量化网格（base64；行主序，255 = nodata）。 */
  values: string;
  /** 每格 1 bit nodata 位图（base64）。 */
  nodataMask: string;
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
  return out;
}

export interface RasterStretchFrame {
  width: number;
  height: number;
  /** RGBA8 行主序（rows = height）；nodata 格 alpha=0。 */
  rgba: Uint8ClampedArray;
}

/**
 * payload → RGBA 帧（与服务端 decode_payload_rgba 同算法；纯函数、确定性）。
 */
export function applyRasterStretch(payload: RasterStretchPayload): RasterStretchFrame {
  if (payload.version !== STRETCH_PAYLOAD_VERSION) {
    throw new Error(`不支持的拉伸 payload 版本: ${payload.version}`);
  }
  const { width, height } = payload;
  const stops = payload.paletteHex.map(hexToRgb);
  const quant = base64ToBytes(payload.values);
  if (quant.length !== width * height) {
    throw new Error(`量化网格尺寸不符: ${quant.length} != ${width * height}`);
  }
  const maskBits = base64ToBytes(payload.nodataMask);
  // clipLo/clipHi 服务端已用于量化归一（payload.stretch 供绝对值域重建与
  // 图例标注；客户端着色只需量化档 → stops 插值）。

  const rgba = new Uint8ClampedArray(width * height * 4);
  const nStops = stops.length;
  for (let i = 0; i < width * height; i += 1) {
    // np.packbits 位序为 MSB-first（服务端权威）：字节内第 b 位的掩码是 1<<(7-b)。
    const byte = maskBits[i >> 3];
    const isNodata = ((byte >> (7 - (i & 7))) & 1) === 1 || quant[i] === QUANT_NODATA;
    if (isNodata) {
      rgba[i * 4 + 3] = 0;
      continue;
    }
    const norm = quant[i] / (QUANT_NODATA - 1);
    const scaled = Math.min(Math.max(norm * (nStops - 1), 0), nStops - 1);
    const lower = Math.floor(scaled);
    const upper = Math.min(lower + 1, nStops - 1);
    const frac = scaled - lower;
    for (let c = 0; c < 3; c += 1) {
      // floor(v+0.5)（四舍五入）与服务端 numpy floor(x+0.5) 逐分支一致
      //（parity 锚）—— 不用 Uint8ClampedArray 的隐式取整。
      rgba[i * 4 + c] = Math.floor(
        stops[lower][c] * (1 - frac) + stops[upper][c] * frac + 0.5,
      );
    }
    rgba[i * 4 + 3] = 255;
  }
  return { width, height, rgba };
}
