import { describe, it, expect } from 'vitest';
import {
  CHART_STATES,
  canTransitionChartState,
  chartStateOperation,
  deriveChartState,
  chartStateToPlacementPatch,
  serializeChartState,
  restoreChartState,
} from './chart-state';
import catalog from '@/lib/map-components/component-catalog.generated.json';

/**
 * V4 图表七态状态机 —— 与后端 chart_kinds.py 词表/迁移表一致性 + 派生/回放。
 */

describe('chart state machine', () => {
  it('七态词表/迁移表/操作前置与后端 catalog 严格一致（一致性锁）', () => {
    expect(CHART_STATES).toHaveLength(7);
    const cat = catalog as unknown as {
      chartStates: string[];
      chartStateTransitions: string[];
      agentChartOperations: Record<string, string[]>;
    };
    expect([...CHART_STATES].sort()).toEqual([...cat.chartStates].sort());
    // 迁移表逐条对账（有向）
    const localEdges: string[] = [];
    for (const a of CHART_STATES) {
      for (const b of CHART_STATES) {
        if (a !== b && canTransitionChartState(a, b)) localEdges.push(`${a}>${b}`);
      }
    }
    expect(localEdges.sort()).toEqual([...cat.chartStateTransitions].sort());
  });

  it('有向迁移：hidden 只能经 visible 回场', () => {
    expect(canTransitionChartState('hidden', 'visible')).toBe(true);
    expect(canTransitionChartState('hidden', 'floating')).toBe(false);
    expect(canTransitionChartState('hidden', 'collapsed')).toBe(false);
    expect(canTransitionChartState('visible', 'floating')).toBe(true);
    expect(canTransitionChartState('floating', 'docked')).toBe(true);
    expect(canTransitionChartState('docked', 'anchored')).toBe(true);
    expect(canTransitionChartState('anchored', 'floating')).toBe(true);
  });

  it('操作 → 状态映射（close/restore/collapse/expand）', () => {
    expect(chartStateOperation('visible', 'close')).toBe('hidden');
    expect(chartStateOperation('hidden', 'restore')).toBe('visible');
    expect(chartStateOperation('floating', 'collapse')).toBe('collapsed');
    expect(chartStateOperation('collapsed', 'expand')).toBe('expanded');
    expect(chartStateOperation('visible', 'expand')).toBe('visible');
    // 不改状态的操作
    expect(chartStateOperation('floating', 'move')).toBeNull();
    expect(chartStateOperation('floating', 'highlight')).toBeNull();
  });

  it('placement/enabled → 状态派生', () => {
    expect(deriveChartState(false, undefined)).toBe('hidden');
    expect(deriveChartState(true, { mode: 'floating', x: 1, y: 1 })).toBe('floating');
    expect(deriveChartState(true, { mode: 'floating', collapsed: true })).toBe('collapsed');
    expect(deriveChartState(true, { mode: 'anchor' }, true)).toBe('docked');
    expect(deriveChartState(true, { mode: 'anchor', collapsed: true })).toBe('collapsed');
    expect(deriveChartState(true, undefined)).toBe('anchored');
  });

  it('状态 → placement 补丁投影（docked 无投影）', () => {
    expect(chartStateToPlacementPatch('floating')).toEqual({ mode: 'floating', collapsed: false });
    expect(chartStateToPlacementPatch('anchored')).toEqual({ mode: 'anchor', collapsed: false });
    expect(chartStateToPlacementPatch('collapsed')).toEqual({ collapsed: true });
    expect(chartStateToPlacementPatch('docked')).toBeNull();
  });

  it('serialization/replay 快照往返', () => {
    const snap = serializeChartState('chart-1', true, { mode: 'floating', x: 10, y: 20 });
    expect(snap.state).toBe('floating');
    const patch = restoreChartState(snap);
    expect(patch).toEqual({
      enabled: true,
      placement: { mode: 'floating', x: 10, y: 20 },
      docked: false,
    });
    // docked 快照往返：docked 标记显式保留（dockSlice 侧恢复）
    const docked = serializeChartState('chart-2', true, { mode: 'anchor' }, true);
    expect(docked.state).toBe('docked');
    expect(restoreChartState(docked)?.docked).toBe(true);
  });
});
