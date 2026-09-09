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
