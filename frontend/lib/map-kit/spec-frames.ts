/**
 * V6（ADR-0120 W9）：spec 级 frames → 导出帧适配层（R1-M4）。
 *
 * MapSpec `layout.frames`（spec 级 atlas，v1.1 additive）与 frame-composer
 * 的请求级 ExportFrame 是两个面：本适配层做显式投影 ——
 * - title / extent 直接映射；
 * - view（center/zoom）→ extent：与后端 publication 同口径换算
 *   （span = 360 / 2^zoom；纬向 span/2，±85 裁剪）；
 * - layerOverrides / pageSize：前端 canvas 导出**不可映射**（MapLibre
 *   setFilter 是等值筛选语义；页面尺寸由画布决定）——由后端 publication
 *   PDF 承接（_apply_frame_overrides / @page size）。前端不伪造。
 *
 * 纯函数、确定性；上限 50 帧与 frame-composer MAX_FRAMES 同口径。
 */
import type { ExportFrame } from './frame-composer';

interface SpecFrameLike {
  id?: string;
  title?: string;
  enabled?: boolean;
  extent?: unknown;
  view?: { center?: unknown; zoom?: unknown };
}

export interface SpecFramesAdapterResult {
  frames: ExportFrame[];
  /** enabled=false 被过滤的帧数（user-wins 关闭）。 */
  disabledCount: number;
  /** view 无 center → 无法定界，跳过的帧数。 */
  unmappableCount: number;
}

function extentOf(frame: SpecFrameLike): [number, number, number, number] | undefined {
  const e = frame.extent;
  if (
    Array.isArray(e) &&
    e.length === 4 &&
    e.every((v) => typeof v === 'number' && Number.isFinite(v))
  ) {
    return e as [number, number, number, number];
  }
  const view = frame.view;
  if (!view || !Array.isArray(view.center) || view.center.length < 2) return undefined;
  const lng = Number(view.center[0]);
  const lat = Number(view.center[1]);
  const zoom = Number(view.zoom ?? 10);
  if (!Number.isFinite(lng) || !Number.isFinite(lat) || !Number.isFinite(zoom)) return undefined;
  const span = 360 / 2 ** zoom;
  // R2-M12：下溢/巨幅 zoom 的结果复检（span 非 finite 或 0 → unmappable）
  if (!Number.isFinite(span) || span <= 0) return undefined;
  const latSpan = span / 2;
  const clamp = (v: number) => Math.max(Math.min(v, 85), -85);
  return [lng - span / 2, clamp(lat - latSpan / 2), lng + span / 2, clamp(lat + latSpan / 2)];
}

export function specFramesToExportFrames(
  frames: SpecFrameLike[] | undefined | null,
): SpecFramesAdapterResult {
  const result: SpecFramesAdapterResult = { frames: [], disabledCount: 0, unmappableCount: 0 };
  if (!Array.isArray(frames)) return result;
  for (const frame of frames) {
    if (!frame || typeof frame !== 'object') continue;
    if (frame.enabled === false) {
      result.disabledCount += 1;
      continue;
    }
    if (result.frames.length >= 50) break; // 与 MAX_FRAMES 同口径截断
    const extent = extentOf(frame);
    if (!extent) {
      result.unmappableCount += 1;
      continue;
    }
    const out: ExportFrame = { extent };
    const title = typeof frame.title === 'string' && frame.title ? frame.title : frame.id;
    if (title) out.title = title;
    result.frames.push(out);
  }
  return result;
}


// ── G15：frame_product 感知的诚实投影 ──────────────────────────────────────

interface ProductFrameLike {
  frame_id?: string;
  title?: string;
  legend?: string;
  data?: {
    where?: { field?: string; value?: unknown; op?: string };
    time_window?: unknown;
    layer_id?: string;
  };
}

interface SpecWithProduct {
  layout?: { frames?: SpecFrameLike[] };
  frame_product?: { enabled?: boolean; frames?: ProductFrameLike[] };
}

export interface ProductFramesResult {
  frames: ExportFrame[];
  /** 产品在场的证据（调用方据此选择后端 publication 产品页路径）。 */
  productPresent: boolean;
  /** 客户端 canvas 无法等价承载的语义（如实披露 —— 近似图不冒充原样）。 */
  limitations: string[];
  /** enabled=false 或无 where/extent 的画幅计数。 */
  disabledCount: number;
  skippedCount: number;
}

/**
 * frame_product 画幅 → 请求级导出帧（canvas 栅格网格路径）。
 *
 * 真实承载：where 等值过滤（ExportFrame.where）+ 范围/标题；
 * 诚实披露：layerOverrides / pageSize / 共享图例带 / 时间窗精确语义
 * 不在客户端等价复刻 —— PDF/vector 需求走后端 publication
 * （atlas.driver=product）。返回 limitations，不静默近似。
 */
export function productFramesToExportFrames(
  spec: SpecWithProduct | null | undefined,
): ProductFramesResult {
  const out: ProductFramesResult = {
    frames: [], productPresent: false, limitations: [],
    disabledCount: 0, skippedCount: 0,
  };
  const product = spec?.frame_product;
  if (!product || product.enabled === false) return out;
  out.productPresent = true;
  const registered = new Map<string, SpecFrameLike>();
  for (const f of spec?.layout?.frames ?? []) {
    if (f && typeof f === 'object' && typeof f.id === 'string') {
      registered.set(f.id, f);
    }
  }
  for (const slot of product.frames ?? []) {
    if (!slot || typeof slot.frame_id !== 'string') continue;
    const frame = registered.get(slot.frame_id);
    if (!frame || frame.enabled === false) {
      out.disabledCount += 1;
      continue;
    }
    const extent = extentOf(frame);
    const where = slot.data?.where;
    const hasWhere =
      !!where && where.op === 'eq' && typeof where.field === 'string'
      && 'value' in (where ?? {});
    if (!extent && !hasWhere) {
      out.skippedCount += 1;
      continue;
    }
    const ef: ExportFrame = hasWhere
      ? {
          where: {
            layerId: String(slot.data?.layer_id ?? ''),
            field: String(where!.field),
            equal: where!.value as string | number,
          },
        }
      : { extent: extent! };
    const title = slot.title ?? frame.title ?? frame.id;
    if (title) ef.title = String(title);
    if (out.frames.length >= 50) break;
    out.frames.push(ef);
  }
  out.limitations = [
    'client_canvas_no_layer_overrides',
    'client_canvas_no_shared_legend_band',
    'client_canvas_no_time_window_exactness',
  ];
  return out;
}
