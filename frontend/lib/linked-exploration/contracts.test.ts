/**
 * G46 前端合同测试：与后端 contract.py 同构（typed identity / 闭开 / null）。
 *
 * oracle 期望值手写（与后端测试同源期望，非实现镜像）。
 */
import { describe, expect, it } from 'vitest';
import {
  compileCombinedWhere,
  conditionToWhere,
  whereToMaplibre,
} from './contracts';

const cat = (values: Array<string | number | boolean | null>, includeNull = false) => ({
  kind: 'category' as const,
  conditionId: 'c1',
  chartId: 'ch1',
  field: 'type',
  values,
  includeNull,
});

const rng = (bounds: { gte?: number; gt?: number; lte?: number; lt?: number }) => ({
  kind: 'range' as const,
  conditionId: 'c2',
  chartId: 'ch2',
  field: 'pop',
  ...bounds,
});

describe('compileCombinedWhere', () => {
  it('条件间 AND，同分类条件内 OR（E02/E03）', () => {
    const w = compileCombinedWhere([cat(['a', 'b']), rng({ gte: 100, lte: 500 })]);
    expect(w).toEqual({
      op: 'and',
      terms: [
        { op: 'in', field: 'type', values: ['a', 'b'] },
        { op: 'and', terms: [
          { op: 'gte', field: 'pop', value: 100 },
          { op: 'lte', field: 'pop', value: 500 },
        ] },
      ],
    });
  });

  it('空条件集 → null（不过滤）', () => {
    expect(compileCombinedWhere([])).toBeNull();
  });

  it('facet 只排除自身控件（E11）', () => {
    const w = compileCombinedWhere(
      [cat(['a']), rng({ gte: 1 }), {
        kind: 'category', conditionId: 'c3', chartId: 'ch3', field: 'zone', values: ['x'],
      }],
      'ch2',
    );
    expect(w).toEqual({
      op: 'and',
      terms: [
        { op: 'in', field: 'type', values: ['a'] },
        { op: 'in', field: 'zone', values: ['x'] },
      ],
    });
  });
});

describe('whereToMaplibre', () => {
  it('typed in 无 to-string（E06）', () => {
    const expr = whereToMaplibre(conditionToWhere(cat([1, '1', true])));
    expect(expr).toEqual(['in', ['get', 'type'], ['literal', [1, '1', true]]]);
  });

  it('eq null → == null（MapLibre 语义：缺失与显式 null 都命中）', () => {
    const expr = whereToMaplibre(conditionToWhere(cat([], true)));
    expect(expr).toEqual(['==', ['get', 'type'], null]);
  });

  it('闭区间两端 >= <=（E08）', () => {
    const expr = whereToMaplibre(conditionToWhere(rng({ gte: 100, lte: 500 })));
    expect(expr).toEqual([
      'all',
      ['>=', ['get', 'pop'], 100],
      ['<=', ['get', 'pop'], 500],
    ]);
  });

  it('开区间 > <（断点闭开规则）', () => {
    const expr = whereToMaplibre(conditionToWhere(rng({ gt: 0, lt: 100 })));
    expect(expr).toEqual([
      'all',
      ['>', ['get', 'pop'], 0],
      ['<', ['get', 'pop'], 100],
    ]);
  });

  it('组合多条件 → 嵌套 all', () => {
    const expr = whereToMaplibre(compileCombinedWhere([cat(['a']), rng({ gte: 10 })])!);
    expect(expr).toEqual([
      'all',
      ['in', ['get', 'type'], ['literal', ['a']]],
      ['>=', ['get', 'pop'], 10],
    ]);
  });

  it('整数值浮点归一（100.0 → 100）', () => {
    const expr = whereToMaplibre(conditionToWhere(rng({ gte: 100.0 })));
    expect((expr as unknown[])[2]).toBe(100);
  });

  it('null where → null', () => {
    expect(whereToMaplibre(null)).toBeNull();
  });
});
