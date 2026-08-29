/**
 * Frontend map-product finalizer — viewport truth lives here (ADR-0081).
 *
 * 服务端 Completion Runtime 校验 desired state（artifact/layer/component/
 * layout），但**相机真相只在前端**（MapLibre uncontrolled、持久化
 * opportunistic）。map_finalization SSE 事件携带 result bbox 与完成态，
 * 前端 finalizer 据此做一次有界的视口校验与修复：
 *
 * - 视口与结果 bbox 相交 → valid（不动相机 —— 用户正在看结果）；
 * - 不相交 → repairable：fitBounds 一次（degenerate bbox 由 navigation
 *   的 minSpan 拓宽；空结果无 bbox → 不修复，绝不 fit 到空集）；
 * - 修复经既有 camera command 通道（用户手势仲裁/中断语义免费获得）。
 *
 * 有界性：每个 finalization 载荷至多一次相机动作；无循环、无重试风暴。
 */

import type { Map } from 'maplibre-gl';
import * as navigation from '@/lib/map-kit/navigation';

/** map_finalization SSE 载荷（后端 finalization_sse_payload 的镜像）。 */
export interface MapFinalizationPayload {
  status: 'pending' | 'needs_repair' | 'complete' | 'failed' | string;
  viewport_status?: string;
  result_bbox?: number[] | null;
  summary?: string;
  issues?: Array<{ code?: string; severity?: string; target?: string }>;
  repairs?: string[];
}

export type ViewportCheck = 'valid' | 'repairable' | 'invalid' | 'not_applicable';

/** 校验 bbox 是否可用于相机判定（4 元、经纬有序）。 */
export function isRepairableBbox(bbox: unknown): bbox is [number, number, number, number] {
  return (
    Array.isArray(bbox) &&
    bbox.length === 4 &&
    bbox.every((v) => typeof v === 'number' && Number.isFinite(v)) &&
    bbox[0] <= bbox[2] &&
    bbox[1] <= bbox[3]
  );
}

/** 视口（LngLatBounds-like）与 bbox 是否相交。 */
export function viewportIntersectsBbox(
  view: { getWest(): number; getSouth(): number; getEast(): number; getNorth(): number },
  bbox: [number, number, number, number],
): boolean {
  return (
    view.getWest() <= bbox[2] &&
    bbox[0] <= view.getEast() &&
    view.getSouth() <= bbox[3] &&
    bbox[1] <= view.getNorth()
  );
}

/**
 * 校验 + 有界修复（每载荷至多一次 fitBounds）。
 * 返回视口判定与是否执行了修复 —— 供命令 ack / dev 日志消费。
 */
export function finalizeViewport(
  map: Map,
  bbox: unknown,
  opts: { padding?: number } = {},
): { check: ViewportCheck; repaired: boolean } {
  if (!isRepairableBbox(bbox)) {
    // 空结果/无空间语义：不 fit（fit 到空集或垃圾值比不动更糟）
    return { check: 'not_applicable', repaired: false };
  }
  try {
    const bounds = map.getBounds();
    if (viewportIntersectsBbox(bounds, bbox)) {
      return { check: 'valid', repaired: false };
    }
  } catch {
    // 地图未就绪（样式未加载）→ 不修复，下一次 finalization 再校验
    return { check: 'invalid', repaired: false };
  }
  // 修复：一次 fitBounds（navigation 内部做 degenerate 拓宽 + maxZoom 上限）
  try {
    navigation.fitBounds(map, bbox, opts.padding ?? 80);
    return { check: 'repairable', repaired: true };
  } catch {
    return { check: 'invalid', repaired: false };
  }
}

/** 用户可见的轻量披露（仅异常态 —— 完成态零噪声）。 */
export function finalizationUserNotice(payload: MapFinalizationPayload): string | null {
  if (payload.status === 'needs_repair') {
    return '地图收尾：部分内容需要关注（视口/图层已尝试自动修复）';
  }
  if (payload.status === 'failed') {
    return '地图未能完成收尾：结果数据不完整，可重试分析';
  }
  return null;
}
