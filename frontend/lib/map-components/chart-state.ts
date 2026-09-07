import type { ComponentPlacement } from '@/lib/mapspec-compiler/types';

/**
 * Chart State Machine — 浮动统计图表七态状态机（Design System V4）.
 *
 * 词表与迁移表是后端 app/lib/cartography/chart_kinds.py 的前端镜像
 * （单一权威在后端；此处镜像由 chart-state.test 锁定一致性）。用户普通
 * UI 操作（FloatingChrome）、Agent 工具调用（chartCommands）、
 * serialization/replay（restoreChartState）三方共用同一状态真值。
 */

export type ChartState =
  | 'hidden'
  | 'visible'
  | 'collapsed'
  | 'expanded'
  | 'floating'
  | 'docked'
  | 'anchored';

export const CHART_STATES: readonly ChartState[] = [
  'hidden', 'visible', 'collapsed', 'expanded', 'floating', 'docked', 'anchored',
];

/** 有向迁移表（与后端 CHART_STATE_TRANSITIONS 同表）。 */
const CHART_STATE_TRANSITIONS: ReadonlySet<string> = new Set([
  'hidden>visible',
  'visible>collapsed', 'visible>expanded',
  'visible>floating', 'visible>docked', 'visible>anchored',
  'collapsed>expanded', 'collapsed>hidden',
  'expanded>collapsed', 'expanded>hidden',
  'floating>collapsed', 'floating>hidden',
  'docked>collapsed', 'docked>hidden',
  'anchored>collapsed', 'anchored>hidden',
  'floating>docked', 'floating>anchored',
  'docked>floating', 'anchored>floating', 'docked>anchored',
]);

export function canTransitionChartState(from: ChartState, to: ChartState): boolean {
  if (from === to) return true;
  return CHART_STATE_TRANSITIONS.has(`${from}>${to}`);
}

/**
 * Agent / 用户共享操作词表（与后端 AGENT_CHART_OPERATIONS 同表）。
 * 返回操作后的目标状态；不需要状态变化的操作（filter/highlight/
 * switch_*）返回 null。
 */
export function chartStateOperation(
  state: ChartState,
  operation: string,
): ChartState | null {
  switch (operation) {
    case 'close':
      return 'hidden';
    case 'restore':
      return 'visible';
    case 'collapse':
      return 'collapsed';
    case 'expand':
      return state === 'collapsed' ? 'expanded' : 'visible';
    case 'pin':
      return 'floating';
    case 'move':
    case 'resize':
    case 'switch_chart_type':
    case 'switch_field':
    case 'filter':
    case 'highlight':
      return null;
    default:
      return null;
  }
}

/**
 * 从组件持久化字段派生当前状态（单向：placement/enabled → state）。
 * - enabled=false → hidden
 * - placement.mode === 'floating' → collapsed 折叠时 'collapsed'，否则 'floating'
 * - docked 态由 dockSlice 承管（刻意不进 MapSpec —— 与既有 dock 契约
 *   一致），placement 无法表达；调用方持有 dock 信息时以参数覆写。
 * - 缺省锚点槽位 → 'anchored'
 */
export function deriveChartState(
  enabled: boolean,
  placement: ComponentPlacement | undefined,
  docked = false,
): ChartState {
  if (!enabled) return 'hidden';
  const mode = placement?.mode;
  if (mode === 'floating') {
    return placement?.collapsed ? 'collapsed' : 'floating';
  }
  if (docked) {
    return placement?.collapsed ? 'collapsed' : 'docked';
  }
  if (placement?.collapsed) return 'collapsed';
  return 'anchored';
}

/**
 * 状态 → placement 补丁（Agent set_state 操作的持久化投影）。
 * 返回 null 表示该状态不能由 placement 表达（如 'visible' 泛态 → 保持现状）。
 */
export function chartStateToPlacementPatch(
  state: ChartState,
): Partial<ComponentPlacement> | null {
  switch (state) {
    case 'hidden':
      return { /* enabled=false 由调用方补 */ };
    case 'collapsed':
      return { collapsed: true };
    case 'expanded':
    case 'visible':
      return { collapsed: false };
    case 'floating':
      return { mode: 'floating', collapsed: false };
    case 'docked':
      // dock 语义由 dockSlice 承管（不进 MapSpec）—— 无 placement 投影
      return null;
    case 'anchored':
      return { mode: 'anchor', collapsed: false };
    default:
      return null;
  }
}

/** serialization：状态 + placement 的可重放快照（与 spec 组件字段同构）。 */
export function serializeChartState(
  componentId: string,
  enabled: boolean,
  placement: ComponentPlacement | undefined,
  docked = false,
): { componentId: string; enabled: boolean; state: ChartState; placement?: ComponentPlacement; docked: boolean } {
  return {
    componentId,
    enabled,
    state: deriveChartState(enabled, placement, docked),
    placement,
    docked,
  };
}

/** replay：快照恢复为 patch（commitComponentPatch 消费）。
 * docked 由 dockSlice 承管（不进 MapSpec）—— 快照显式携带 docked 标记，
 * 调用方据其恢复 dock 侧信息；placement 只投影 MapSpec 可表达部分。
 * 非法状态（词表外）返回 null —— 词表内任意自迁移恒合法。 */
export function restoreChartState(
  snap: ReturnType<typeof serializeChartState>,
): { enabled?: boolean; placement?: Partial<ComponentPlacement>; docked: boolean } | null {
  if (!(CHART_STATES as readonly string[]).includes(snap.state)) return null;
  return {
    enabled: snap.enabled,
    ...(snap.placement ? { placement: snap.placement } : {}),
    docked: snap.docked,
  };
}
