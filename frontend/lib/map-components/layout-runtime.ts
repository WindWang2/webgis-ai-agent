/**
 * ComponentLayoutRuntime —— 锚定组件的确定性槽位布局求解器（v2 Phase 9）。
 *
 * #1079：chart/statistics/annotation 缺省同为 top-left 却互不感知（各自
 * positionClass 锚定同一点互相遮挡）；bottom 侧有 U-2（#884）的堆叠原语
 * 但只在 bottom 槽生效、且硬编码在 helpers 里。本模块把槽位模型统一起来：
 *
 *   slot        —— top-left/center/right、bottom-left/center/right、floating
 *   priority    —— 槽内堆叠序（小者更贴边；0 贴边，1,2,… 依序远离）
 *   exclusive   —— 同槽语义上的"独占"标记（title 于 top-center；信息性，
 *                   求解器不丢弃组件，只保证排序稳定）
 *   stackStepPx —— 槽内层距（缺省 36px，与 HUD chrome 族约定一致）
 *
 * 求解是纯函数：输入组件列表（type + 可选显式 position），输出每个组件的
 * (slot, stackIndex)。top 槽向下堆叠（top 偏移递增），bottom 槽向上堆叠
 * （bottom 偏移递增，复用既有 U-2 约定：scale_bar 恒贴底）。单组件槽不产
 * 生偏移（index 0 不消费）。
 */

export type LayoutSlot =
  | 'top-left' | 'top-center' | 'top-right'
  | 'bottom-left' | 'bottom-center' | 'bottom-right'
  | 'floating' | 'none';

export interface ComponentLayoutMeta {
  defaultSlot: LayoutSlot;
  priority: number;
  exclusive?: boolean;
  stackStepPx?: number;
}

export const DEFAULT_STACK_STEP_PX = 36;

/** 组件类型的布局元数据（与后端 component-catalog defaultPosition 对齐）。 */
export const COMPONENT_LAYOUT_META: Record<string, ComponentLayoutMeta> = {
  title: { defaultSlot: 'top-center', priority: 0, exclusive: true },
  subtitle: { defaultSlot: 'top-center', priority: 1 },
  north_arrow: { defaultSlot: 'top-right', priority: 0 },
  scale_bar: { defaultSlot: 'bottom-right', priority: 0 },
  attribution: { defaultSlot: 'bottom-left', priority: 0 },
  continuous_colorbar: { defaultSlot: 'bottom-right', priority: 1 },
  legend: { defaultSlot: 'bottom-left', priority: 1 },
  categorical_legend: { defaultSlot: 'bottom-left', priority: 2 },
  annotation: { defaultSlot: 'top-left', priority: 0 },
  statistics_panel: { defaultSlot: 'top-left', priority: 1 },
  chart_panel: { defaultSlot: 'top-left', priority: 2 },
};

export interface SlotLayoutEntry {
  slot: LayoutSlot;
  /** 槽内堆叠序（0 贴边）。单组件槽也赋 0（消费方自行跳过偏移）。 */
  index: number;
  /** 槽内组件总数（≤1 时消费方无需偏移）。 */
  slotSize: number;
}

export type SlottedComponent = {
  type: string;
  position?: string;
};

/**
 * 确定性求解：显式 position ?? 类型 defaultSlot 分槽；槽内按
 * (priority, 声明序) 排序赋 index。输出 Map 保插入序（声明序），
 * 值携带槽位裁决。
 */
export function resolveSlotLayout<T extends SlottedComponent>(
  components: T[],
): Map<T, SlotLayoutEntry> {
  const groups = new Map<LayoutSlot, T[]>();
  for (const c of components) {
    const meta = COMPONENT_LAYOUT_META[c.type];
    const slot = (
      (c.position as LayoutSlot | undefined) ?? meta?.defaultSlot ?? 'none'
    );
    if (slot === 'none' || slot === 'floating') {
      // floating/隐藏不参与锚定堆叠
      continue;
    }
    const bucket = groups.get(slot);
    if (bucket) bucket.push(c);
    else groups.set(slot, [c]);
  }
  const slotOrder = new Map<LayoutSlot, T[]>();
  for (const [slot, bucket] of groups) {
    // scale_bar 恒贴底（U-2 约定）：priority 视作 -1
    const sorted = [...bucket].sort((a, b) => {
      const pa = a.type === 'scale_bar' ? -1 : (COMPONENT_LAYOUT_META[a.type]?.priority ?? 50);
      const pb = b.type === 'scale_bar' ? -1 : (COMPONENT_LAYOUT_META[b.type]?.priority ?? 50);
      if (pa !== pb) return pa - pb;
      return 0; // 稳定排序保声明序
    });
    slotOrder.set(slot, sorted);
  }
  const result = new Map<T, SlotLayoutEntry>();
  for (const c of components) {
    const meta = COMPONENT_LAYOUT_META[c.type];
    const slot = (
      (c.position as LayoutSlot | undefined) ?? meta?.defaultSlot ?? 'none'
    );
    if (slot === 'none' || slot === 'floating') continue;
    const bucket = slotOrder.get(slot) ?? [];
    result.set(c, {
      slot,
      index: Math.max(0, bucket.indexOf(c)),
      slotSize: bucket.length,
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
