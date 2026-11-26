/**
 * 运行状态 → 画布 overlay 投影纯函数（模型构建器运行视图）。
 *
 * 词表对齐纪律：
 * - 节点态词表 = workflow_runtime NodeState 九值（PENDING/READY/RUNNING/
 *   SUCCEEDED/FAILED/BLOCKED/SKIPPED/CANCELLED/STALE）；
 * - 色相与 components/sidebar/workflow/runtime-inspector.tsx 的 STATE_BADGE
 *   逐条同族（emerald→success / blue→info / red→critical / amber,orange→
 *   warning / 灰系→ink-muted），但落仓库语义 token（stroke-status-*）而非
 *   裸 Tailwind 调色板 —— 与画布其他描边同一套颜色语言；
 * - 实例态词表 = InstanceStatus 五值（running/succeeded/failed/cancelled/
 *   superseded）。
 * 纯函数：无 React、无 fetch —— 画布只消费映射结果。
 */

import { authorNodeId } from '@/lib/workflow/model-graph';

/** 实例 wire node_id → 作者 node_id（transform:<op>:<author> rsplit 可逆）。 */
export { authorNodeId };

/** 节点态 → 画布节点描边色（语义 token；未登记态回退中性）。 */
export const NODE_STATE_STROKE: Record<string, string> = {
  RUNNING: 'stroke-status-info',
  SUCCEEDED: 'stroke-status-success',
  FAILED: 'stroke-status-critical',
  BLOCKED: 'stroke-status-warning',
  STALE: 'stroke-status-warning',
  CANCELLED: 'stroke-ink-muted',
  SKIPPED: 'stroke-ink-muted',
  READY: 'stroke-status-info',
  PENDING: 'stroke-ink-muted',
};

/** 节点态 → 徽标文字色（与 STATE_BADGE 文字色同族；列表徽标用）。 */
export const NODE_STATE_TEXT: Record<string, string> = {
  SUCCEEDED: 'text-status-success',
  RUNNING: 'text-status-info',
  FAILED: 'text-status-critical',
  BLOCKED: 'text-status-warning',
  STALE: 'text-status-warning',
  CANCELLED: 'text-ink-muted',
  SKIPPED: 'text-ink-muted',
  READY: 'text-status-info',
  PENDING: 'text-ink-muted',
};

/** 实例终态（轮询停止条件）。 */
const TERMINAL_INSTANCE_STATUSES = new Set(['succeeded', 'failed', 'cancelled', 'superseded']);

export function isTerminalInstanceStatus(status: string): boolean {
  return TERMINAL_INSTANCE_STATUSES.has(status);
}

/** 实例态 → InlineNotice variant（running 不在映射内 → 'info'）。 */
export function instanceStatusNoticeVariant(
  status: string,
): 'success' | 'error' | 'warning' | 'info' {
  if (status === 'succeeded') return 'success';
  if (status === 'failed') return 'error';
  if (status === 'cancelled' || status === 'superseded') return 'warning';
  return 'info';
}

/** 实例态 → i18n 键（modelBuilder.run.status<Name>；未登记态回退原文）。 */
export function instanceStatusI18nKey(status: string): string {
  const known = new Set([
    'running', 'succeeded', 'failed', 'cancelled', 'superseded',
  ]);
  return known.has(status) ? `run.status${status.charAt(0).toUpperCase()}${status.slice(1)}` : '';
}

/** 节点态 → i18n 键（modelBuilder.run.status<NAME>；未登记态回退原文）。 */
export function nodeStateI18nKey(state: string): string {
  const known = new Set([
    'RUNNING', 'SUCCEEDED', 'FAILED', 'BLOCKED', 'CANCELLED',
    'PENDING', 'READY', 'SKIPPED', 'STALE',
  ]);
  return known.has(state) ? `run.status${state.charAt(0)}${state.slice(1).toLowerCase()}` : '';
}

/** 节点态是否展示错误徽标（FAILED/BLOCKED 点击显示 error_code 的画布信号）。 */
export function isFailingNodeState(state: string): boolean {
  return state === 'FAILED' || state === 'BLOCKED';
}

/**
 * wire 节点投影数组 → {作者 id: 状态} 映射（画布描边/徽标消费；transform
 * wire id 经 authorNodeId 还原作者 id，同名覆盖以后到为准）。
 */
export function nodeStateMap(
  nodes: { node_id: string; state: string }[],
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const n of nodes) out[authorNodeId(n.node_id)] = n.state;
  return out;
}
