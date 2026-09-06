import type { CommandEntry, MapCommandResult } from './types';
import { devOnly } from '@/lib/utils/logger';
import { commitComponentPatch } from '@/lib/mapspec/component-mutation';
import { canTransitionChartState, deriveChartState, chartStateToPlacementPatch } from '@/lib/map-components/chart-state';
import type { ChartState } from '@/lib/map-components/chart-state';
import type { ComponentPlacement } from '@/lib/mapspec-compiler/types';
import { getMapSpecSessionCursor, getCommittedMapSpec } from '@/lib/mapspec/session-cursor';
import { publishSelection } from '@/lib/selection/selection-store';

/**
 * chart_* commands — Agent 可控浮动统计图表（Design System V4）.
 *
 * 与用户 FloatingChrome 手势共用同一突变通道（commitComponentPatch →
 * mapspec/mutations patch_component，ownerToken + expected_revision CAS）：
 * 用户拖拽、Agent move、serialization/replay 三方操作同一份 spec 真相，
 * 不出现第二套图表状态。
 *
 * 合法性：状态迁移按 chart-state 有向表校验（与后端词表同源）；目标组件
 * 必须是 chart_panel；非法操作返回 failed（不静默吞掉）。
 */

type ChartComponentView = {
  id: string;
  type: string;
  enabled?: boolean;
  placement?: ComponentPlacement;
};

function findChartComponent(getHudState: () => unknown, componentId: string): ChartComponentView | null {
  // 组件真相在 committed MapSpec 文档（唯一 desired cartographic state）。
  void getHudState;
  const spec = getCommittedMapSpec() as { layout?: { components?: ChartComponentView[] } } | null;
  const comp = spec?.layout?.components?.find((c) => c.id === componentId);
  if (!comp) return null;
  if (comp.type !== 'chart_panel') return null;
  return comp;
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
    run(ctx): MapCommandResult {
      const { componentId, state } = ctx.params as { componentId: string; state: string };
      const comp = findChartComponent(ctx.getHudState, componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      const from = deriveChartState(comp.enabled !== false, comp.placement);
      const to = state as ChartState;
      if (!canTransitionChartState(from, to)) {
        return fail(`illegal chart state transition: ${from} -> ${state}`);
      }
      if (state === 'hidden') {
        void commitComponentPatch(componentId, { enabled: false });
        return ok({ componentId, state: 'hidden' });
      }
      const patch = chartStateToPlacementPatch(state as ChartState);
      if (patch === null) {
        return fail(`state '${state}' has no placement projection (dock 由面板坞承管)`);
      }
      void commitComponentPatch(componentId, {
        enabled: true,
        placement: { ...(comp.placement ?? {}), ...patch } as ComponentPlacement,
      });
      return ok({ componentId, state });
    },
  },

  /** 图表移动（floating x/y 或锚点槽位迁移）。 */
  chart_move: {
    requiredParams: (p) => typeof p.componentId === 'string' && (typeof p.x === 'number' || typeof p.anchor === 'string'),
    run(ctx): MapCommandResult {
      const { componentId, x, y, anchor } = ctx.params as { componentId: string; x?: number; y?: number; anchor?: string };
      const comp = findChartComponent(ctx.getHudState, componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      if (typeof x === 'number' && typeof y === 'number') {
        void commitComponentPatch(componentId, {
          placement: { ...(comp.placement ?? {}), mode: 'floating', x, y },
        });
        return ok({ componentId, x, y });
      }
      if (typeof anchor === 'string') {
        void commitComponentPatch(componentId, {
          placement: { mode: 'anchor', anchor, collapsed: false },
        });
        return ok({ componentId, anchor });
      }
      return fail('chart_move requires x/y (floating) or anchor (slot)');
    },
  },

  /** 图表缩放（floating width/height）。 */
  chart_resize: {
    requiredParams: (p) => typeof p.componentId === 'string' && typeof p.width === 'number',
    run(ctx): MapCommandResult {
      const { componentId, width, height } = ctx.params as { componentId: string; width?: number; height?: number };
      const comp = findChartComponent(ctx.getHudState, componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      void commitComponentPatch(componentId, {
        placement: {
          ...(comp.placement ?? {}),
          mode: 'floating',
          width,
          ...(typeof height === 'number' ? { height } : {}),
        } as ComponentPlacement,
      });
      return ok({ componentId, width, height });
    },
  },

  /** 图表类型切换（kind 变体通道 —— 与组件目录同词表）。 */
  chart_switch_type: {
    requiredParams: (p) => typeof p.componentId === 'string' && typeof p.chartType === 'string',
    run(ctx): MapCommandResult {
      const { componentId, chartType } = ctx.params as { componentId: string; chartType: string };
      const comp = findChartComponent(ctx.getHudState, componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      void commitComponentPatch(componentId, { variant: chartType });
      return ok({ componentId, chartType });
    },
  },

  /** 图表联动高亮（chart→map 选择语义；经 selection store 广播）。 */
  chart_highlight: {
    requiredParams: (p) => typeof p.componentId === 'string' && Array.isArray(p.categories),
    run(ctx): MapCommandResult {
      const { componentId, categories } = ctx.params as { componentId: string; categories: string[] };
      const comp = findChartComponent(ctx.getHudState, componentId);
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
    run(ctx): MapCommandResult {
      const { componentId } = ctx.params as { componentId: string };
      const comp = findChartComponent(ctx.getHudState, componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      void commitComponentPatch(componentId, { enabled: false });
      return ok({ componentId, state: 'hidden' });
    },
  },
  chart_restore: {
    requiredParams: (p) => typeof p.componentId === 'string',
    run(ctx): MapCommandResult {
      const { componentId } = ctx.params as { componentId: string };
      const comp = findChartComponent(ctx.getHudState, componentId);
      if (!comp) return fail(`chart component not found: ${componentId}`);
      void commitComponentPatch(componentId, { enabled: true });
      return ok({ componentId, state: 'visible' });
    },
  },
};

// 折叠/展开是 set_state 的糖（显式命令对 UI 语义更友好）
chartCommands.chart_collapse = {
  requiredParams: (p) => typeof p.componentId === 'string',
  run(ctx) {
    return chartCommands.chart_set_state.run({
      ...ctx,
      params: { ...ctx.params, state: 'collapsed' } as typeof ctx.params,
    }) as MapCommandResult;
  },
};
chartCommands.chart_expand = {
  requiredParams: (p) => typeof p.componentId === 'string',
  run(ctx) {
    return chartCommands.chart_set_state.run({
      ...ctx,
      params: { ...ctx.params, state: 'expanded' } as typeof ctx.params,
    }) as MapCommandResult;
  },
};

// devOnly 保持与相邻命令切片一致的日志口径
void devOnly;
