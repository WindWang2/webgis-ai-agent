/**
 * G46 condition-store：多条件并存、分域清空、过滤投影、toggle-off。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import {
  getExplorationGeneration,
  bindExploration,
  clearAllConditions,
  clearChartConditions,
  getCombinedWhere,
  getExplorationFilterExpression,
  listConditions,
  registerChart,
  removeChart,
  removeCondition,
  setCondition,
  unbindExploration,
} from './condition-store';

beforeEach(() => {
  unbindExploration();
  bindExploration('ref:test-1', 3, 'layer-a');
});

describe('多条件并存', () => {
  it('两个控件的条件并存（E02），互相不顶掉', () => {
    setCondition({
      kind: 'category', conditionId: 'cond-ch1', chartId: 'ch1', field: 'type',
      values: ['a', 'b'], includeNull: false,
    });
    setCondition({
      kind: 'range', conditionId: 'cond-ch2', chartId: 'ch2', field: 'pop',
      gte: 10, lte: 99,
    });
    expect(listConditions()).toHaveLength(2);
    const where = getCombinedWhere();
    expect(where?.op).toBe('and');
  });

  it('同 conditionId 幂等替换；空分类条件自动移除（toggle-off）', () => {
    setCondition({
      kind: 'category', conditionId: 'cond-ch1', chartId: 'ch1', field: 'type',
      values: ['a'], includeNull: false,
    });
    expect(listConditions()).toHaveLength(1);
    setCondition({
      kind: 'category', conditionId: 'cond-ch1', chartId: 'ch1', field: 'type',
      values: ['a', 'b'], includeNull: false,
    });
    expect(listConditions()[0].kind === 'category'
      && (listConditions()[0] as { values: unknown[] }).values).toEqual(['a', 'b']);
    setCondition({
      kind: 'category', conditionId: 'cond-ch1', chartId: 'ch1', field: 'type',
      values: [], includeNull: false,
    });
    expect(listConditions()).toHaveLength(0);
  });

  it('removeCondition 只删一个（E04：移除区间保留分类）', () => {
    setCondition({
      kind: 'category', conditionId: 'cond-ch1', chartId: 'ch1', field: 'type',
      values: ['a'], includeNull: false,
    });
    setCondition({
      kind: 'range', conditionId: 'cond-ch2', chartId: 'ch2', field: 'pop', gte: 10,
    });
    removeCondition('cond-ch2');
    expect(listConditions()).toHaveLength(1);
    expect(listConditions()[0].kind).toBe('category');
  });

  it('clearChartConditions 与 clearAllConditions 范围不同（E05）', () => {
    setCondition({
      kind: 'category', conditionId: 'cond-ch1', chartId: 'ch1', field: 'type',
      values: ['a'], includeNull: false,
    });
    setCondition({
      kind: 'range', conditionId: 'cond-ch2', chartId: 'ch2', field: 'pop', gte: 10,
    });
    clearChartConditions('ch1');
    expect(listConditions()).toHaveLength(1);
    expect(listConditions()[0].chartId).toBe('ch2');
    clearAllConditions();
    expect(listConditions()).toHaveLength(0);
  });

  it('关闭图表窗口 → 该控件条件一并移除（E16 可解释策略）', () => {
    registerChart({ chartId: 'ch1', kind: 'category', field: 'type', aggregation: 'count', title: '', bins: 10 });
    setCondition({
      kind: 'category', conditionId: 'cond-ch1', chartId: 'ch1', field: 'type',
      values: ['a'], includeNull: false,
    });
    setCondition({
      kind: 'range', conditionId: 'cond-ch2', chartId: 'ch2', field: 'pop', gte: 1,
    });
    removeChart('ch1');
    expect(listConditions().map((c) => c.conditionId)).toEqual(['cond-ch2']);
  });
});

describe('过滤投影', () => {
  it('绑定层才投影；其他层 null（E17：不越层污染）', () => {
    setCondition({
      kind: 'category', conditionId: 'cond-ch1', chartId: 'ch1', field: 'type',
      values: [1, '1', true], includeNull: false,
    });
    const expr = getExplorationFilterExpression('layer-a');
    expect(expr).toEqual(['in', ['get', 'type'], ['literal', [1, '1', true]]]);
    expect(getExplorationFilterExpression('layer-other')).toBeNull();
  });

  it('无条件 → null（地图不过滤，非永假）', () => {
    expect(getExplorationFilterExpression('layer-a')).toBeNull();
  });

  it('unbind 后投影清零（会话/面板关闭）', () => {
    setCondition({
      kind: 'category', conditionId: 'cond-ch1', chartId: 'ch1', field: 'type',
      values: ['a'], includeNull: false,
    });
    unbindExploration();
    expect(getExplorationFilterExpression('layer-a')).toBeNull();
    expect(listConditions()).toHaveLength(0);
  });
});

describe('竞态世代（E23/E24 语义锚）', () => {
  it('条件连续变化 → generation 递增（旧响应按世代丢弃的依据）', () => {
    bindExploration('ref:t', 1, 'layer-a');
    const g0 = getExplorationGeneration();
    setCondition({
      kind: 'range', conditionId: 'c1', chartId: 'ch', field: 'v', gte: 1,
    });
    const g1 = getExplorationGeneration();
    setCondition({
      kind: 'range', conditionId: 'c1', chartId: 'ch', field: 'v', gte: 2,
    });
    const g2 = getExplorationGeneration();
    expect(g1).toBeGreaterThan(g0);
    expect(g2).toBeGreaterThan(g1);
  });

  it('组合 where 随最新条件集确定（B 迟到不覆盖：digest 单值）', async () => {
    const { compileCombinedWhere } = await import('./contracts');
    bindExploration('ref:t', 1, 'layer-a');
    // 快速连续刷选 A→B：最终态只有 B 的 where（A 的响应即使迟到也因
    // generation 不匹配被 runPreview 丢弃 —— store 层保证最终态一致性）
    setCondition({ kind: 'range', conditionId: 'c', chartId: 'ch', field: 'v', gte: 100 });
    setCondition({ kind: 'range', conditionId: 'c', chartId: 'ch', field: 'v', gte: 200 });
    expect(compileCombinedWhere(listConditions())).toEqual({
      op: 'gte', field: 'v', value: 200,
    });
  });
});
