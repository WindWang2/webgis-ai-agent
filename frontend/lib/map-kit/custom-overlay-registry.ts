/**
 * Runtime Layer Mount Registry —— imperative `custom-*` 覆盖层的重挂账本。
 *
 * #1078(G-1/FE1, v2 Phase 5)：MapLibre `setStyle()`（basemap 切换 / 自愈
 * watchdog）会抹掉一切命令式覆盖层。spec 承载层由 reconcile 全量重放，
 * 但 `add_layer` / `add_native_heatmap` / `create_thematic_map` /
 * `add_heatmap_raster` / `add_raster_layer` 写入的 `custom-*` 层只存在于
 * MapLibre 里 —— 旧实现只有 `raiseCustomOverlayLayers`（z-raise，无法复活
 * 已被清除的层），basemap 切换后这些覆盖层**永久消失**。
 *
 * 本注册表在 renderer 的挂载缝（addGeoJsonSource / addImageSource /
 * addVectorLayer）记录 `custom-` 前缀的 source/layer 定义，style reload
 * 完成后由 `remountCustomOverlays` 按插入序重放（addSource → addLayer）。
 *
 * 生命周期：
 *  - 命令删除覆盖层（remove_layer 等）必须 `unregisterCustomOverlay`，
 *    否则重挂会复活已删除的层（layer resurrection 违例）；
 *  - 会话 id 变化时 `clearCustomOverlayRegistry`（旧会话的命令层不属于
 *    新会话；见 session-cursor 的切换清理）；
 *  - 记录的是**原始**定义（GeoJSON 记录 raw data，不含 viewport 裁剪）。
 */

export interface CustomOverlaySourceDef {
  kind: 'geojson' | 'image';
  data?: any;
  url?: string;
  coordinates?: [[number, number], [number, number], [number, number], [number, number]];
}

export interface CustomOverlayLayerDef {
  id: string;
  type: string;
  source: string;
  paint?: Record<string, unknown>;
  layout?: Record<string, unknown>;
  filter?: unknown;
  beforeId?: string;
}

const sources = new Map<string, CustomOverlaySourceDef>();
const layers = new Map<string, CustomOverlayLayerDef>();

function isCustomId(id: string | undefined | null): boolean {
  return typeof id === 'string' && id.startsWith('custom-');
}

export function recordCustomOverlaySource(id: string, def: CustomOverlaySourceDef): void {
  if (!isCustomId(id)) return;
  sources.set(id, def);
}

export function recordCustomOverlayLayer(def: CustomOverlayLayerDef): void {
  if (!isCustomId(def?.id)) return;
  layers.set(def.id, def);
}

/** 命令删除覆盖层时反注册（层族 + 其独占 source）。幂等。 */
export function unregisterCustomOverlay(layerId: string): void {
  for (const id of [...layers.keys()]) {
    if (id === layerId || id.startsWith(`${layerId}-`) || id.startsWith(`${layerId}__`)) {
      const def = layers.get(id);
      layers.delete(id);
      if (def?.source) sources.delete(def.source);
    }
  }
}

export function clearCustomOverlayRegistry(): void {
  sources.clear();
  layers.clear();
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
  if (!map) return 0;
  let remounted = 0;
  // 先 sources 后 layers（MapLibre 层引用 source，顺序反了会 throw）。
  for (const [id, def] of sources) {
    if (map.getSource?.(id)) continue;
    try {
      if (def.kind === 'geojson') {
        map.addSource(id, { type: 'geojson', data: def.data });
      } else if (def.kind === 'image' && def.url && def.coordinates) {
        map.addSource(id, { type: 'image', url: def.url, coordinates: def.coordinates });
      }
    } catch { /* 竞态下已被补挂/样式未就绪 —— 下次重放再试 */ }
  }
  for (const def of layers.values()) {
    if (map.getLayer?.(def.id)) continue;
    try {
      const layer: Record<string, unknown> = {
        id: def.id,
        type: def.type,
        source: def.source,
      };
      if (def.paint) layer.paint = def.paint;
      if (def.layout) layer.layout = def.layout;
      if (def.filter !== undefined) layer.filter = def.filter;
      map.addLayer(layer as any, def.beforeId ?? undefined);
      // v2(review R4-P1-3)：重挂的层必须补记 style-layer-id 账本 —— 否则
      // 下一次 layer-changing reconcile 的 z 序同步看不到 custom 层，
      // #461 的"埋没"缺陷在 style 重载后复现。
      hooks?.onLayerAdded?.(map, def.id);
      remounted += 1;
    } catch { /* 同上 */ }
  }
  return remounted;
}

/** 测试观测点：当前注册的层 id（插入序）。 */
export function listCustomOverlayLayerIds(): string[] {
  return [...layers.keys()];
}
