/**
 * G36 地图采集 registry（ADR-0291）。
 *
 * MapPanel 挂载时登记活地图实例的采集函数；聊天侧（侧栏 composer）经
 * `captureCurrentMap()` 触发一次受控采集 —— 等待渲染稳定（既有
 * waitForRenderSettle），复用 captureMapCanvas，返回：
 *   blob + 设备像素尺寸 + DPR + 视口身份（center/zoom/bearing/pitch）。
 *
 * 明确边界（V07）：只采集 map canvas —— **不含 DOM 图例/标题/页面 chrome**；
 * 调用方在证据 label 里如实标注「地图视口（不含页面图例）」。
 */
import { captureMapCanvas } from '@/lib/map-kit/exporter';
import { waitForRenderSettle } from '@/lib/mapspec-runtime/render-observation';

export interface MapCaptureResult {
  blob: Blob;
  /** 画布设备像素尺寸（MapLibre canvas.width/height）。 */
  deviceWidth: number;
  deviceHeight: number;
  dpr: number;
  viewport: {
    center: [number, number];
    zoom: number;
    bearing: number;
    pitch: number;
  };
}

type CaptureFn = () => Promise<MapCaptureResult | null>;

let activeCapture: { capture: CaptureFn } | null = null;

/** MapPanel 注册/释放（mount → register；unmount/map 重建 → 重注册）。 */
export function registerMapCapture(fn: CaptureFn | null): () => void {
  const registration = fn ? { capture: fn } : null;
  activeCapture = registration;
  return () => {
    if (activeCapture === registration) activeCapture = null;
  };
}

/** 当前主图是否可采集（不可采集时 UI 显示禁用原因）。 */
export function mapCaptureAvailable(): boolean {
  return activeCapture !== null;
}

export async function captureCurrentMap(): Promise<MapCaptureResult | null> {
  const capture = activeCapture?.capture;
  if (!capture) return null;
  try {
    return await capture();
  } catch {
    return null;
  }
}

/** 由活 MapLibre 实例构造采集函数（MapPanel onLoad 注册用）。 */
export function makeMapCapture(map: unknown): CaptureFn {
  return async () => {
    const m = map as {
      getCanvas: () => HTMLCanvasElement;
      getCenter: () => { lng: number; lat: number };
      getZoom: () => number;
      getBearing: () => number;
      getPitch: () => number;
    } | null;
    if (!m || typeof m.getCanvas !== 'function') return null;
    // 渲染稳定门：瓦片/字体未 settle 时等待（有限时延，超时按现状采集
    // 并在 observations 里如实披露 —— 不伪造“必然完整”）。
    await waitForRenderSettle(m as never);
    const blob = await captureMapCanvas(m as never);
    if (!blob || blob.size === 0) return null;
    const canvas = m.getCanvas();
    const center = m.getCenter();
    return {
      blob,
      deviceWidth: canvas.width,
      deviceHeight: canvas.height,
      dpr: typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1,
      viewport: {
        center: [center.lng, center.lat],
        zoom: m.getZoom(),
        bearing: m.getBearing(),
        pitch: m.getPitch(),
      },
    };
  };
}
