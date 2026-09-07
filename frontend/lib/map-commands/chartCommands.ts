import type { CommandEntry, MapCommandResult } from './types';
import { devOnly } from '@/lib/utils/logger';
import { commitComponentPatch, getComponentPlacementOverride } from '@/lib/mapspec/component-mutation';
import { canTransitionChartState, deriveChartState, chartStateToPlacementPatch } from '@/lib/map-components/chart-state';
import type { ChartState } from '@/lib/map-components/chart-state';
import type { ComponentPlacement } from '@/lib/mapspec-compiler/types';
import { getCommittedMapSpec } from '@/lib/mapspec/session-cursor';
import { publishSelection } from '@/lib/selection/selection-store';
import catalog from '@/lib/map-components/component-catalog.generated.json';

/**
 * chart_* commands — Agent 可控浮动统计图表（Design System V4）.
 *
 * 与用户 FloatingChrome 手势共用同一突变通道（commitComponentPatch →
 * mapspec/mutations patch_component，ownerToken + expected_revision CAS）：
 * 用户拖拽、Agent move、serialization/replay 三方操作同一份 spec 真相，
 * 不出现第二套图表状态。
 *
 * 诚实性契约：
 * - 命令 **await** 突变结果：失败（非 409）→ failed；409 superseded
 *   （服务端已有更新真相，通常是用户并发操作优先）→ failed 并说明收敛，
 *   不谎报 succeeded；
 * - placement 读侧取 override（用户未落盘的最新手势）优先于 committed
 *   spec，消除读-改-写窗口的丢失更新；
 * - move/resize/set_state 全部经 chart-state 有向迁移表校验（hidden 只能
 *   经 visible 回场；resize 前置 floating）；
 * - switch_type 按 catalog chartKinds 词表校验（violin planned 拒绝）。
 */

type ChartComponentView = {
  id: string;
  type: string;
  enabled?: boolean;
  placement?: ComponentPlacement;
};

type PatchOutcome = { status: 'applied' | 'superseded' };

const CHART_KIND_IDS: ReadonlySet<string> = new Set(
  ((catalog as unknown as { chartKinds?: { id: string; liveEngine: string }[] }).chartKinds ?? [])
    .filter((k) => k.liveEngine !== 'planned')
    .map((k) => k.id),
);

function findChartComponent(componentId: string): ChartComponentView | null {
  // 组件真相在 committed MapSpec 文档（唯一 desired cartographic state）。
  const spec = getCommittedMapSpec() as { layout?: { components?: ChartComponentView[] } } | null;
  const comp = spec?.layout?.components?.find((c) => c.id === componentId);
  if (!comp) return null;
  if (comp.type !== 'chart_panel') return null;
  return comp;
}

/** 读侧：用户手势 override 优先（未落盘的最新真相），committed spec 兜底。 */
function currentPlacement(comp: ChartComponentView): ComponentPlacement | undefined {
  return getComponentPlacementOverride(comp.id) ?? comp.placement;
}

async function applyPatch(
  componentId: string,
  patch: Parameters<typeof commitComponentPatch>[1],
): Promise<MapCommandResult> {
  try {
    const outcome = (await commitComponentPatch(componentId, patch)) as PatchOutcome | void;
    if (outcome && outcome.status === 'superseded') {
      return {
        status: 'failed',
        error: 'superseded: 服务端已有更新真相（用户并发操作优先），补丁已丢弃并收敛',
      };
    }
    return { status: 'succeeded', result: patch };
  } catch (err) {
    devOnly.warn('[chartCommands] patch failed:', err);
    return { status: 'failed', error: `patch failed: ${String(err)}` };
  }
}

function ok(result: unknown): MapCommandResult {
  return { status: 'succeeded', result };
}

function fail(error: string): MapCommandResult {
  return { status: 'failed', error };
}

export const chartCommands: Record<string, CommandEntry> = {
  /** 图表状态设置（hidden/visible/collapsed/expanded/floating/anchored）。 */
  chart_set_state: {
    requiredParams: (p) => typeof p.componentId === 'string' && typeof p.state === 'string',
    async run(ctx): Promise<MapCommandResult> {
      const { componentId, state } = ctx.params as { componentId: string; state: string };
      const comp = findChartComponent(componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      const placement = currentPlacement(comp);
      const from = deriveChartState(comp.enabled !== false, placement);
      const to = state as ChartState;
      if (!canTransitionChartState(from, to)) {
        return fail(`illegal chart state transition: ${from} -> ${state}`);
      }
      if (state === 'hidden') {
        return applyPatch(componentId, { enabled: false });
      }
      const patch = chartStateToPlacementPatch(to);
      if (patch === null) {
        return fail(`state '${state}' has no placement projection (dock 由面板坞承管)`);
      }
      return applyPatch(componentId, {
        enabled: true,
        placement: { ...(placement ?? {}), ...patch } as ComponentPlacement,
      });
    },
  },

  /** 图表移动：x/y → floating；anchor → anchored（均经迁移表校验）。 */
  chart_move: {
    requiredParams: (p) => typeof p.componentId === 'string' && (typeof p.x === 'number' || typeof p.anchor === 'string'),
    async run(ctx): Promise<MapCommandResult> {
      const { componentId, x, y, anchor } = ctx.params as { componentId: string; x?: number; y?: number; anchor?: string };
      if (x !== undefined && anchor !== undefined) {
        return fail('chart_move: x/y 与 anchor 互斥，一次只迁移一种模式');
      }
      const comp = findChartComponent(componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      const placement = currentPlacement(comp);
      const from = deriveChartState(comp.enabled !== false, placement);
      if (typeof x === 'number' && typeof y === 'number') {
        if (!canTransitionChartState(from, 'floating')) {
          return fail(`illegal transition: ${from} -> floating（hidden 图表请先 restore）`);
        }
        return applyPatch(componentId, {
          enabled: true,
          placement: { ...(placement ?? {}), mode: 'floating', x, y },
        });
      }
      if (typeof anchor === 'string') {
        if (!canTransitionChartState(from, 'anchored')) {
          return fail(`illegal transition: ${from} -> anchored（hidden 图表请先 restore）`);
        }
        return applyPatch(componentId, {
          enabled: true,
          placement: { mode: 'anchor', anchor, collapsed: false },
        });
      }
      return fail('chart_move requires x/y (floating) or anchor (slot)');
    },
  },

  /** 图表缩放：前置 floating（与后端 AGENT_CHART_OPERATIONS 前置一致）。 */
  chart_resize: {
    requiredParams: (p) => typeof p.componentId === 'string' && typeof p.width === 'number',
    async run(ctx): Promise<MapCommandResult> {
      const { componentId, width, height } = ctx.params as { componentId: string; width?: number; height?: number };
      const comp = findChartComponent(componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      const placement = currentPlacement(comp);
      const from = deriveChartState(comp.enabled !== false, placement);
      if (from !== 'floating') {
        return fail(`resize requires floating state (current: ${from})—— 请先 pin 为浮动`);
      }
      return applyPatch(componentId, {
        placement: {
          ...(placement ?? {}),
          mode: 'floating',
          width,
          ...(typeof height === 'number' ? { height } : {}),
        } as ComponentPlacement,
      });
    },
  },

  /** 图表类型切换（kind 变体通道；词表按 catalog chartKinds 校验）。 */
  chart_switch_type: {
    requiredParams: (p) => typeof p.componentId === 'string' && typeof p.chartType === 'string',
    async run(ctx): Promise<MapCommandResult> {
      const { componentId, chartType } = ctx.params as { componentId: string; chartType: string };
      const comp = findChartComponent(componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      if (!CHART_KIND_IDS.has(chartType)) {
        return fail(`unknown or planned chart kind '${chartType}'（violin 未实现，请用 box_plot/histogram）`);
      }
      return applyPatch(componentId, { variant: chartType });
    },
  },

  /** 图表联动高亮（chart→map 选择语义；经 selection store 广播）。 */
  chart_highlight: {
    requiredParams: (p) => typeof p.componentId === 'string' && Array.isArray(p.categories),
    run(ctx): MapCommandResult {
      const { componentId, categories } = ctx.params as { componentId: string; categories: string[] };
      const comp = findChartComponent(componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      publishSelection('select', {
        source: 'chart',
        layer_id: componentId,
        selected_categories: categories.slice(0, 20),
      });
      return ok({ componentId, categories });
    },
  },

  /** 图表关闭/恢复（enabled 通道；hidden 只能经 visible 回场）。 */
  chart_close: {
    requiredParams: (p) => typeof p.componentId === 'string',
    async run(ctx): Promise<MapCommandResult> {
      const { componentId } = ctx.params as { componentId: string };
      const comp = findChartComponent(componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      // 幂等：已 hidden 时重复 close 是合法 no-op
      return applyPatch(componentId, { enabled: false });
    },
  },
  chart_restore: {
    requiredParams: (p) => typeof p.componentId === 'string',
    async run(ctx): Promise<MapCommandResult> {
      const { componentId } = ctx.params as { componentId: string };
      const comp = findChartComponent(componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      // 幂等：visible 态重复 restore 是合法 no-op
      return applyPatch(componentId, { enabled: true });
    },
  },
};

// devOnly 保持与相邻命令切片一致的日志口径
void devOnly;
