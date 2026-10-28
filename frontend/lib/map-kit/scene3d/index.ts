/**
 * scene3d — MapSpec v1.7 三维场景资产渲染内核（G3D02）。
 *
 * 公开面（renderer.ts / map-panel 消费；其余为内部模块）：
 * - syncSceneAssets / disposeSceneAssets / describeScene3dAssets（renderer 缝）
 * - pickScene3d（交互层）
 * - sceneAssetStatusSnapshot / recordSceneAssetStatus（观察披露）
 * - 纯函数层（geo/gltf-parser/tileset/scheduler）独立可测。
 */

import type { MapSpec } from '@/lib/mapspec-compiler/types.generated';
import { getScene3dManager } from './asset-manager';

export { Scene3DManager, getScene3dManager } from './asset-manager';
export type { Scene3dPickHit, Scene3dSyncOptions } from './asset-manager';
export {
  sceneAssetStatusSnapshot,
  sceneAssetLatestStatuses,
  recordSceneAssetStatus,
  resetSceneAssetStatusForTests,
} from './status';
export type { SceneAssetStatusEvent, SceneAssetStatusCode } from './status';
export { isAllowedUrl, resolveUri, fetchJson, fetchBinary, Scene3dResourceFailure } from './resource';

/** renderer 缝：幂等差分同步（spec.scene.assets → 挂载集）。 */
export function syncSceneAssets(
  map: object,
  spec: MapSpec | null | undefined,
): { mounted: number; removed: number; updated: number } {
  return getScene3dManager(map).sync(spec);
}

/** renderer 缝：全部释放（map unmount / WebGL 重建）。 */
export function disposeSceneAssets(map: object): void {
  getScene3dManager(map).dispose();
}

/** renderer 缝：WebGL context lost —— 停加载/释放 GPU 侧/资产标 stale。 */
export function scene3dContextLost(map: object): void {
  getScene3dManager(map).handleContextLost();
}

/** renderer 缝：WebGL context restored —— 有限重建（幂等差分重挂）。 */
export function scene3dContextRestored(map: object, spec: MapSpec | null | undefined): void {
  getScene3dManager(map).handleContextRestored(spec);
}

/** 观察面：跨资产累计预算用量。 */
export function scene3dBudgetUsage(map: object): ReturnType<import('./asset-manager').Scene3DManager['budgetUsage']> {
  return getScene3dManager(map).budgetUsage();
}

/** 表象面（M5，user-wins 本地覆盖 —— 不进 spec/不持久）。 */
export function scene3dSetAssetPresentation(
  map: object,
  assetId: string,
  patch: { visible?: boolean; opacity?: number },
): boolean {
  return getScene3dManager(map).setAssetPresentation(assetId, patch);
}

export {
  applyLightingPreset as scene3dApplyLightingPreset,
  presetLighting,
} from './custom-layer';
export type { Scene3dLightingPreset } from './custom-layer';

/** 观察面。 */
export function describeScene3dAssets(map: object): ReturnType<import('./asset-manager').Scene3DManager['describe']> {
  return getScene3dManager(map).describe();
}

/** 交互面：屏幕像素拾取。 */
export function pickScene3d(
  map: object,
  px: number,
  py: number,
  canvasW: number,
  canvasH: number,
): import('./asset-manager').Scene3dPickHit | null {
  return getScene3dManager(map).pick(px, py, canvasW, canvasH);
}
