/**
 * composition-repair —— live 侧冲突自愈执行器（AC-07 / ADR-0156 §P1）。
 *
 * 后端 semantic_checks 产出修复**建议**（plan_layout_repairs 同链）；
 * 本模块在 live 渲染管线执行同一策略链：改 anchor → 缩尺寸 → 折叠进
 * 溢出面板 → 隐藏最低优先组件。每一步产出 RepairStep 轨迹（进
 * CompositionDescriptor.decisions —— 可审计，09 线评审/10 线回归同源）。
 *
 * 契约：
 * - user-pinned（origin==='user'）组件绝不被动挪动/折叠/隐藏（user wins）；
 * - 无碰撞 → 零动作零改动（与 V4 求解器的无冲突等价性同款回归锁定）；
 * - 纯函数：同输入同输出；不改 MapSpec 语义状态（desired state 归
 *   resolver/用户手势所有，这里产出的是渲染期 derived 裁决）。
 */

import {
  COMPONENT_LAYOUT_META,
  resolveComponentLayout,
  type LayoutParticipant,
} from '@/lib/map-components/resolve-layout';

import type { RepairActionKind, RepairStep } from './composition-descriptor';

/** 可折叠面板族（与后端 COLLAPSIBLE_TYPES 面板子集对齐 —— live 侧以
 * 前端 COLLAPSIBLE_PANEL_TYPES 为基、扩展披露族）。 */
const COLLAPSIBLE_LIVE_TYPES: ReadonlySet<string> = new Set([
  'statistics_panel', 'chart_panel', 'table_panel',
  'decision_panel', 'uncertainty_panel', 'methodology_note',
]);

/** 槽位遍历序（确定性跨槽兜底；与后端 _ZONES 同表）。 */
const ZONES = ['top-left', 'top-center', 'top-right',
  'bottom-left', 'bottom-center', 'bottom-right'] as const;

/** 溢出槽（折叠倾泻目标；后端 V4 L3 同语义）。 */
const OVERFLOW_SLOT = 'bottom-center';

export interface CompositionRepairInput {
  participants: LayoutParticipant[];
  canvas?: { width: number; height: number };
}

export interface CompositionRepairPlan {
  steps: RepairStep[];
  /** id → 改派锚点（渲染期 derived；不写回 spec placement）。 */
  anchorOverrides: Map<string, string>;
  /** 折叠为溢出面板的组件 id。 */
  collapseIds: Set<string>;
  /** 建议隐藏的组件 id（仅 optional/auto —— user 组件绝不在此）。 */
  hideIds: Set<string>;
}

function _isUser(p: LayoutParticipant): boolean {
  return p.origin === 'user';
}

/**
 * 对共享求解器的碰撞输出执行策略链。确定性：参与者按 (priority, 声明序)
 * 处理；每个未收容者逐级降级并记录步骤。
 */
export function planCompositionRepairs(
  input: CompositionRepairInput,
): CompositionRepairPlan {
  const { participants, canvas } = input;
  const steps: RepairStep[] = [];
  const anchorOverrides = new Map<string, string>();
  const collapseIds = new Set<string>();
  const hideIds = new Set<string>();
  if (!participants.length) {
    return { steps, anchorOverrides, collapseIds, hideIds };
  }

  const solved = resolveComponentLayout(participants, canvas);
  const collisions = solved.collisions;
  const collisionIds = new Set<string>();
  for (const c of collisions) {
    if (c.kind === 'slot-capacity' || c.kind === 'floating-anchor') {
      collisionIds.add(c.a);
    }
  }
  // floating×floating 只披露（user-pinned 域）—— 修复建议仅对可折叠
  // 的 auto/agent 面板给 collapse（user wins：不挪不藏）。
  for (const c of collisions) {
    if (c.kind !== 'floating-floating') continue;
    for (const pid of [c.a, c.b]) {
      const p = participants.find((q) => q.id === pid);
      if (p && !_isUser(p) && COLLAPSIBLE_LIVE_TYPES.has(p.type)) {
        if (!collapseIds.has(pid)) {
          collapseIds.add(pid);
          steps.push({
            action: 'collapse_to_overflow', componentId: pid,
            componentType: p.type, reason: 'floating_overlap:user_wins_advisory',
          });
        }
      }
    }
  }

  const occupied = new Map<string, number>();
  for (const [pid, slot] of solved.slots) {
    if (!collisionIds.has(pid)) {
      occupied.set(slot.slot, (occupied.get(slot.slot) ?? 0) + 1);
    }
  }

  // 处理序：声明序（与求解器主序一致 —— 稳定且可复现）
  for (const p of participants) {
    if (!collisionIds.has(p.id) || _isUser(p)) continue;
    const slot = solved.slots.get(p.id);
    const from = slot?.slot ?? 'none';
    let resolved = false;

    // L1 改 anchor：跨槽确定性兜底（现有槽容量外）
    for (const zone of ZONES) {
      if (zone === from) continue;
      const used = occupied.get(zone) ?? 0;
      const cap = zone === 'top-center' ? 2 : 3;
      if (used < cap) {
        occupied.set(zone, used + 1);
        anchorOverrides.set(p.id, zone);
        steps.push({
          action: 'change_anchor', componentId: p.id, componentType: p.type,
          from, to: zone, reason: 'slot_capacity_live',
        });
        resolved = true;
        break;
      }
    }

    // L2 缩尺寸：live 侧以 compact 建议承载（COLLAPSIBLE 族折叠语义同域）
    if (!resolved && (COMPONENT_LAYOUT_META[p.type]?.stackStepPx ?? 0) > 36) {
      steps.push({
        action: 'shrink', componentId: p.id, componentType: p.type,
        from, reason: 'stack_step_compact',
      });
    }

    // L3 折叠为溢出面板
    if (!resolved && COLLAPSIBLE_LIVE_TYPES.has(p.type)) {
      collapseIds.add(p.id);
      steps.push({
        action: 'collapse_to_overflow', componentId: p.id,
        componentType: p.type, from, to: OVERFLOW_SLOT,
        reason: 'collapsed_overflow_panel',
      });
      resolved = true;
    }

    // L4 隐藏最低优先（仅 auto/agent optional —— 已经 user 过滤）
    if (!resolved) {
      hideIds.add(p.id);
      steps.push({
        action: 'hide_lowest_priority', componentId: p.id,
        componentType: p.type, from, reason: 'unresolvable_optional_live',
      });
    }
  }

  return { steps, anchorOverrides, collapseIds, hideIds };
}

/** RepairStep → CompositionDecision（中间层 decisions 段装配）。 */
export function repairStepsToDecisions(
  steps: RepairStep[],
  firstStep = 0,
): Array<{ step: number; kind: 'repair'; componentId: string; componentType: string; before?: string; after?: string; reason: string }> {
  return steps.map((s, i) => ({
    step: firstStep + i,
    kind: 'repair' as const,
    componentId: s.componentId,
    componentType: s.componentType,
    before: s.from,
    after: s.to,
    reason: `${s.action}:${s.reason}`,
  }));
}

export type { RepairActionKind };
