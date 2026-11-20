/**
 * G15 多画幅相机同步组（纯函数 —— comparison-sync 的 N 方推广）。
 *
 * 纪律与 ComparisonView 相同：同步决策收敛为纯函数，返回 null = 已收敛
 * （幂等，二次应用 no-op），运行时 isSyncing 标志防事件回环 —— 两层防御。
 * 组语义：同 sync_group 的画幅共享相机维度；不同组互不传播；无组独立。
 */
import {
  applyCameraPatch,
  readCamera,
  resolveSyncPair,
  type CameraPatch,
  type CameraSnapshot,
} from '@/components/map/comparison/comparison-sync';

export { readCamera, applyCameraPatch };
export type { CameraSnapshot, CameraPatch };

export interface SyncTarget<T> {
  id: string;
  map: T;
  syncGroup: string | null;
}

export interface SyncOutcome<T> {
  /** 需要写入补丁的目标（同组内除源以外、未收敛者）。 */
  patches: Array<{ id: string; map: T; patch: CameraPatch }>;
}

/**
 * 源相机事件 → 同组其余成员的补丁集。
 *
 * 纯决策：source 不在组（null）时无传播；每对 (source, target) 走
 * resolveSyncPair（容差内已一致 → 无补丁 —— 回声环的构造性断点）。
 */
export function resolveGroupSync<T extends Parameters<typeof readCamera>[0]>(
  sourceId: string,
  sourceCamera: CameraSnapshot,
  targets: Array<SyncTarget<T> & { camera: CameraSnapshot }>,
  syncPan: boolean,
  syncZoom: boolean,
): SyncOutcome<T> {
  const source = targets.find((t) => t.id === sourceId);
  const group = source?.syncGroup ?? null;
  const patches: SyncOutcome<T>['patches'] = [];
  if (!group || (!syncPan && !syncZoom)) return { patches };
  for (const target of targets) {
    if (target.id === sourceId) continue;
    if (target.syncGroup !== group) continue; // 不同组/无组不传播
    const patch = resolveSyncPair(sourceCamera, target.camera, syncPan, syncZoom);
    if (patch) patches.push({ id: target.id, map: target.map, patch });
  }
  return { patches };
}

/** 组成员表（面板显示 + 调试）：group → 成员 id 列表（保序）。 */
export function syncGroupMembers(
  frames: Array<{ frameId: string; syncGroup: string | null }>,
): Map<string, string[]> {
  const groups = new Map<string, string[]>();
  for (const f of frames) {
    if (!f.syncGroup) continue;
    const list = groups.get(f.syncGroup) ?? [];
    list.push(f.frameId);
    groups.set(f.syncGroup, list);
  }
  return groups;
}
