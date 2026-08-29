/**
 * #1078(G-1): 命令式 custom-* 覆盖层的重挂闭包登记 —— facade
 * （GIS Runtime v3, Phase B）。
 *
 * v2 曾是独立的第二套事实源（重挂闭包 Map，LRU 64）—— 其
 * remountCustomOverlays 在生产代码中**从未被调用**（真正的重放走
 * map-kit 定义账本），闭包被 remember/forget 维护却永不重放，纯死重。
 *
 * v3(A4)：双账本收敛为唯一的 `runtime-layer-registry`。本 facade 保留原
 * 导出面（heatmapCommands / layerCommands / session-cursor 调用方零改动），
 * 闭包作为**定义重放的兜底**登记进 canonical registry —— 仅当某条目没有
 * layerDef（挂载路径不经 renderer 缝）时，style reload 重放才回退到闭包。
 */

import {
  rememberRuntimeRemount,
  remountRuntimeLayers,
  resetRuntimeLayerRegistry,
  runtimeRemountProviderCount,
  unregisterRuntimeLayer,
} from '../map-kit/runtime-layer-registry';

type RemountFn = (map: any) => void;

/** 挂载成功后登记重挂闭包（兜底路径；定义重放优先）。 */
export function rememberCustomOverlay(id: string, remount: RemountFn): void {
  rememberRuntimeRemount(id, remount);
}

/** 覆盖层被显式移除时注销（避免重挂幽灵；与定义账目同一存储）。 */
export function forgetCustomOverlay(id: string): void {
  unregisterRuntimeLayer(id);
}

export function rememberedCustomOverlayCount(): number {
  return runtimeRemountProviderCount();
}

/**
 * 补挂地图上缺失的登记覆盖层（委托 canonical 重放：定义优先，闭包兜底）。
 * 保留 v2 导出签名；返回重挂数（可观测超集）。
 */
export function remountCustomOverlays(map: unknown): number {
  return remountRuntimeLayers(map as any);
}

/** 测试隔离：清空登记簿（canonical 存储）。 */
export function resetCustomOverlayRegistry(): void {
  resetRuntimeLayerRegistry();
}
