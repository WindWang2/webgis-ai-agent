/**
 * ComponentLayoutRuntime —— 锚定组件槽位布局的 live 侧适配层（v2 Phase 9）。
 *
 * ADR-0084：槽位求解的**单一实现**在 resolve-layout.ts（live chrome 与
 * canvas exporter 共用 —— 两侧各一套半成品求解器是 parity 分叉的根因，
 * review E-1/E-6）。本模块保留既有 API（resolveSlotLayout /
 * COMPONENT_LAYOUT_META / keyboardMoveDelta），内部委托共享求解器：
 *
 *   slot        —— top/bottom-left/center/right（floating/none 不参与锚定堆叠）
 *   priority    —— 槽内堆叠序（小者更贴边；scale_bar 恒贴边，U-2 约定）
 *   anchor      —— 解析后的锚点优先于旧 position 字段（E-6 修复：live 顶槽
 *                  此前只认 position，placement.anchor 被忽略）
 */

import {
  COMPONENT_LAYOUT_META as _SOLVER_META,
  DEFAULT_STACK_STEP_PX as _SOLVER_STEP,
  resolveComponentLayout,
  type ComponentLayoutMeta,
} from './resolve-layout';

export type LayoutSlot =
  | 'top-left' | 'top-center' | 'top-right'
  | 'bottom-left' | 'bottom-center' | 'bottom-right'
  | 'floating' | 'none';

export type { ComponentLayoutMeta };
export const COMPONENT_LAYOUT_META: Record<string, ComponentLayoutMeta> = _SOLVER_META;
export const DEFAULT_STACK_STEP_PX = _SOLVER_STEP;

export interface SlotLayoutEntry {
  slot: LayoutSlot;
  /** 槽内堆叠序（0 贴边）。单组件槽也赋 0（消费方自行跳过偏移）。 */
  index: number;
  /** 槽内组件总数（≤1 时消费方无需偏移）。 */
  slotSize: number;
}

export type SlottedComponent = {
  type: string;
  /** 解析后的锚点（placement.anchor > position > 默认）—— 优先于 position。 */
  anchor?: string;
  position?: string;
};

/**
 * 确定性求解（委托 resolve-layout，共享实现）：anchor ?? position ?? 类型
 * defaultSlot 分槽；槽内按 (priority, 声明序) 排序赋 index。输出 Map 保
 * 插入序（声明序），值携带槽位裁决。
 */
export function resolveSlotLayout<T extends SlottedComponent>(
  components: T[],
): Map<T, SlotLayoutEntry> {
  // 对象身份 → 稳定字符串 id（共享求解器按 id 键输出）
  const idOf = new Map<T, string>();
  const participants = components.map((c, i) => {
    const id = `c${i}`;
    idOf.set(c, id);
    const explicit = (
      c.anchor && c.anchor !== '' ? c.anchor : c.position
    ) as string | undefined;
    // 显式 floating/none → 排除（不参与锚定堆叠）；无显式 → 类型默认槽
    let slot: string;
    if (explicit === 'floating' || explicit === 'none') {
      slot = 'none';
    } else if (explicit) {
      slot = explicit;
    } else {
      slot = _SOLVER_META[c.type]?.defaultSlot ?? 'none';
    }
    return {
      id,
      type: c.type,
      anchor: slot as never,
      floating: false,
      origin: 'auto' as const,
    };
  });

  const solved = resolveComponentLayout(participants);
  const result = new Map<T, SlotLayoutEntry>();
  for (const c of components) {
    const entry = solved.slots.get(idOf.get(c)!);
    if (!entry) continue;
    result.set(c, {
      slot: entry.slot as LayoutSlot,
      index: entry.index,
      slotSize: entry.slotSize,
    });
  }
  return result;
}

/** 键盘步进（#1079）：方向键 8px，Shift/Alt+方向键 24px。 */
export function keyboardMoveDelta(key: string, large: boolean): { dx: number; dy: number } | null {
  const step = large ? 24 : 8;
  switch (key) {
    case 'ArrowUp': return { dx: 0, dy: -step };
    case 'ArrowDown': return { dx: 0, dy: step };
    case 'ArrowLeft': return { dx: -step, dy: 0 };
    case 'ArrowRight': return { dx: step, dy: 0 };
    default: return null;
  }
}
