/**
 * scene3d/status — 资产状态环（G3D02 M4/M7；G3DV2-B 增 cancelled/context_lost）。
 *
 * 有界（64 条）per-asset 状态事件环：加载/挂载/失败/降级/取消/上下文丢失
 * 的观察披露面，供 render-observation 附带上行（后端 telemetry/质量面可见）。
 * 与 adapter 的 sceneEvidence 环同款纪律：只登记不裁决，去重快照。
 */

export type SceneAssetStatusCode =
  | 'mounted'
  | 'unmounted'
  | 'loading'
  | 'load_failed'
  | 'unavailable'
  | 'degraded'
  /** G3DV2-B：加载中途被取消（revision 换代/卸载/任务取消打断在途加载）。 */
  | 'cancelled'
  /** G3DV2-B：WebGL context lost —— 资产停加载、GPU 侧已释放、等待恢复。 */
  | 'context_lost';

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
  mirrorToWindow();
}

/**
 * dev-only 观察窗（map-panel `__mapErrors` 同款纪律：生产零暴露、有界、
 * 仅开发诊断/旅程断言消费）。jsdom/测试环境无 window 时静默跳过。
 */
function mirrorToWindow(): void {
  if (typeof window === 'undefined' || process.env.NODE_ENV !== 'development') return;
  try {
    (window as unknown as { __scene3dAssets?: unknown }).__scene3dAssets = ring.slice(-16);
  } catch { /* 环境异常不影响主链 */ }
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
