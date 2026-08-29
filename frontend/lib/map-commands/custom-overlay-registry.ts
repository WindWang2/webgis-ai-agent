/**
 * #1078(G-1): 命令式 custom-* 覆盖层的挂载登记簿。
 *
 * basemap 切换触发 MapLibre setStyle —— 所有非 style 层/源被整体丢弃。
 * spec 承载层由 MapSpecRuntime 的恢复 reconcile 重建，注记层有自己的重挂
 * （#460），唯独命令路径（add_layer / add_raster_layer /
 * create_thematic_map / add_heatmap_raster）铸的 custom-* 层此前只被
 * z-raise（#461）而不被重挂 —— 一次 basemap 切换即永久消失。
 *
 * 登记闭包在挂载成功时捕获重挂所需的全部输入（geojson/URL/样式），
 * remountCustomOverlays 在每次 reconcile 收尾时补挂地图上缺失的登记项
 * （幂等：getLayer 命中即跳过）。有界 LRU（默认 64）防长会话无限增长。
 */

type RemountFn = (map: any) => void;

const MAX_REMEMBERED = 64;
const registry = new Map<string, RemountFn>();

/** 挂载成功后登记重挂闭包（同 id 覆盖旧闭包并刷新 LRU 位次）。 */
export function rememberCustomOverlay(id: string, remount: RemountFn): void {
  if (!id) return;
  registry.delete(id);
  registry.set(id, remount);
  while (registry.size > MAX_REMEMBERED) {
    const oldest = registry.keys().next().value;
    if (oldest === undefined) break;
    registry.delete(oldest);
  }
}

/** 覆盖层被显式移除时注销（避免重挂幽灵）。 */
export function forgetCustomOverlay(id: string): void {
  registry.delete(id);
}

export function rememberedCustomOverlayCount(): number {
  return registry.size;
}

/**
 * 补挂地图上缺失的登记覆盖层。调用点：reconcile 收尾（与
 * raiseCustomOverlayLayers 并列）—— basemap 切换后的恢复 reconcile 之后
 * 正是覆盖层被 wipe 的时刻。
 */
export function remountCustomOverlays(map: unknown): void {
  if (!map || typeof (map as { getLayer?: unknown }).getLayer !== 'function') return;
  for (const [id, remount] of registry) {
    try {
      if ((map as { getLayer: (id: string) => unknown }).getLayer(id)) continue;
      remount(map);
    } catch {
      // 单项重挂失败不阻断其余项；下一轮 reconcile 再试。
    }
  }
}

/** 测试隔离：清空登记簿。 */
export function resetCustomOverlayRegistry(): void {
  registry.clear();
}
