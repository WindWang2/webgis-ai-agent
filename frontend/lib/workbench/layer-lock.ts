/**
 * Layer lock gate（Workbench V5 / W2）—— agent 突变通道的用户意图护栏。
 *
 * V4 的 lockedLayerIds 只护 UI（面板/批量/隔离）；agent 的 visibility 事务
 * 与 remove_layer 直接绕过（layers-tab.tsx 自认缺口）。V5 把「锁定」统一为
 * typed 门：任何 agent 通道在身份解析后立即分区 allowed/locked：
 *   - 全部目标被锁 → status:'failed' + error:'layer_locked'（ack 通道的
 *     机器可读冲突；后端 ack.error 为自由字符串，无 schema 变更）；
 *   - 部分被锁 → 只应用未锁目标，result 附 locked_layer_ids（不静默）。
 * 用户 override = 显式解锁（toggleLayerLocked，既有路径），agent 永不代解锁。
 */
import { useHudStore } from '@/lib/store/useHudStore';

/** ack.error 词表：图层被用户锁定（机器可读 typed conflict）。 */
export const LOCK_CONFLICT_ERROR = 'layer_locked';

export interface LockPartition {
  allowed: string[];
  locked: string[];
}

/**
 * 按当前锁定集分区目标。lockedLayerIds 缺省时读 HUD store（生产路径）；
 * 测试/纯函数路径可显式传入。顺序保持入参序（z-order 语义不被扰动）。
 */
export function partitionByLock(
  layerIds: readonly string[],
  lockedLayerIds?: readonly string[],
): LockPartition {
  const lockedSet = new Set(
    lockedLayerIds ?? useHudStore.getState().lockedLayerIds ?? [],
  );
  const allowed: string[] = [];
  const locked: string[] = [];
  for (const id of layerIds) {
    (lockedSet.has(id) ? locked : allowed).push(id);
  }
  return { allowed, locked };
}
