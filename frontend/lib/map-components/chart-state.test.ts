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
  it('七态词表与后端 catalog 导出一致', () => {
    expect(CHART_STATES).toHaveLength(7);
    const manifestStates = (catalog as unknown as { chartStates?: string[] }).chartStates;
    if (manifestStates) {
      expect([...CHART_STATES].sort()).toEqual([...manifestStates].sort());
    }
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
    });
  });
});
