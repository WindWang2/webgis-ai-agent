/**
 * Custom Overlay Mount Registry —— facade（GIS Runtime v3, Phase B）。
 *
 * #1078(G-1/FE1, v2 Phase 5)：MapLibre `setStyle()`（basemap 切换 / 自愈
 * watchdog）会抹掉一切命令式覆盖层。spec 承载层由 reconcile 全量重放，
 * 但 `add_layer` / `add_native_heatmap` / `create_thematic_map` /
 * `add_heatmap_raster` / `add_raster_layer` 写入的 `custom-*` 层只存在于
 * MapLibre 里 —— 旧实现只有 `raiseCustomOverlayLayers`（z-raise，无法复活
 * 已被清除的层），basemap 切换后这些覆盖层**永久消失**。
 *
 * v3(A4)：本模块曾是命令式覆盖层的一套独立事实源（source/layer 定义
 * Map），与 `lib/map-commands/custom-overlay-registry.ts`（重挂闭包 Map）
 * 互不知道 —— 双事实源。现两套 facade 都委托唯一的
 * `runtime-layer-registry`（adapter ≠ second storage）；本文件保持原
 * 导出面（renderer 挂载缝 / map-panel 重放 / layerCommands 反注册 /
 * session-cursor 清账的调用方零改动）。
 *
 * 生命周期（语义不变）：
 *  - 命令删除覆盖层（remove_layer 等）必须 `unregisterCustomOverlay`，
 *    否则重挂会复活已删除的层（layer resurrection 违例）；
 *  - 会话 id 变化时 `clearCustomOverlayRegistry`（旧会话的命令层不属于
 *    新会话；见 session-cursor 的切换清理）；
 *  - 记录的是**原始**定义（GeoJSON 记录 raw data，不含 viewport 裁剪）。
 */

import {
  clearRuntimeLayerRegistry,
  listRuntimeLayerIds,
  recordRuntimeLayer,
  recordRuntimeSource,
  remountRuntimeLayers,
  unregisterRuntimeLayer,
} from './runtime-layer-registry';
import type { RuntimeLayerDef, RuntimeLayerSourceDef } from './runtime-layer-registry';

/** 兼容类型名（旧导出面；实现即 canonical 描述符的子集）。 */
export type CustomOverlaySourceDef = RuntimeLayerSourceDef;
export type CustomOverlayLayerDef = RuntimeLayerDef;

function isCustomId(id: string | undefined | null): boolean {
  return typeof id === 'string' && id.startsWith('custom-');
}

export function recordCustomOverlaySource(id: string, def: {
  kind: 'geojson' | 'image';
  data?: any;
  url?: string;
  coordinates?: [[number, number], [number, number], [number, number], [number, number]];
}): void {
  if (!isCustomId(id)) return;
  recordRuntimeSource(id, def);
}

export function recordCustomOverlayLayer(def: {
  id: string;
  type: string;
  source: string;
  paint?: Record<string, unknown>;
  layout?: Record<string, unknown>;
  filter?: unknown;
  beforeId?: string;
}): void {
  if (!isCustomId(def?.id)) return;
  recordRuntimeLayer(def);
}

/** 命令删除覆盖层时反注册（层族 + 其独占 source）。幂等。 */
export function unregisterCustomOverlay(layerId: string): void {
  unregisterRuntimeLayer(layerId);
}

export function clearCustomOverlayRegistry(): void {
  clearRuntimeLayerRegistry();
}

/**
 * style reload 后重放全部已注册覆盖层（幂等：缺失才补，存在则跳过）。
 * 返回重挂的层数（测试/证据观测点）。重放后由调用方执行 z-raise
 * （raiseCustomOverlayLayers）恢复 custom 带的置顶序。
 */
export function remountCustomOverlays(
  map: any,
  hooks?: { onLayerAdded?: (map: any, id: string) => void },
): number {
  return remountRuntimeLayers(map, hooks);
}

/** 测试观测点：当前注册的层 id（插入序）。 */
export function listCustomOverlayLayerIds(): string[] {
  return listRuntimeLayerIds();
}
