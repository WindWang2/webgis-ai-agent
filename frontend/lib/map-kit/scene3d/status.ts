/**
 * scene3d/status — 资产状态环（G3D02 M4/M7）。
 *
 * 有界（64 条）per-asset 状态事件环：加载/挂载/失败/降级的观察披露面，
 * 供 render-observation 附带上行（后端 telemetry/质量面可见）。与
 * adapter 的 sceneEvidence 环同款纪律：只登记不裁决，去重快照。
 */

export type SceneAssetStatusCode =
  | 'mounted'
  | 'unmounted'
  | 'loading'
  | 'load_failed'
  | 'unavailable'
  | 'degraded';

export interface SceneAssetStatusEvent {
  assetId: string;
  code: SceneAssetStatusCode;
  /** typed 原因（错误码/消息首 120 字符）。 */
  reason?: string;
  at: number;
}

const RING_MAX = 64;
let ring: SceneAssetStatusEvent[] = [];

export function recordSceneAssetStatus(
  assetId: string,
  code: SceneAssetStatusCode,
  reason?: string,
): void {
  if (!assetId) return;
  ring.push({
    assetId,
    code,
    reason: reason ? String(reason).slice(0, 120) : undefined,
    at: Date.now(),
  });
  if (ring.length > RING_MAX) ring = ring.slice(-RING_MAX);
}

/** 去重快照（同资产同码只留最新一条；环序返回）。 */
export function sceneAssetStatusSnapshot(): SceneAssetStatusEvent[] {
  const seen = new Set<string>();
  const out: SceneAssetStatusEvent[] = [];
  for (let i = ring.length - 1; i >= 0; i--) {
    const e = ring[i];
    const key = `${e.assetId}\u0000${e.code}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.unshift(e);
  }
  return out;
}

/** 每资产终态视图（mounted/unavailable/...的最新记录）。 */
export function sceneAssetLatestStatuses(): Map<string, SceneAssetStatusEvent> {
  const latest = new Map<string, SceneAssetStatusEvent>();
  for (const e of ring) latest.set(e.assetId, e);
  return latest;
}

export function resetSceneAssetStatusForTests(): void {
  ring = [];
}
