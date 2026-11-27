/**
 * 读者筛选谓词映射测试（WP3）—— where AST / MapLibre filter / 白名单防御。
 */
import { describe, expect, it } from 'vitest';

import {
  describePredicates,
  predicatesToMapLibreFilter,
  predicatesToWhere,
  predicatesWithinWhitelist,
} from './reader-filter';
import type { ReaderPredicate } from './reader-types';

const p = (field: string, op: ReaderPredicate['op'], value: string | number): ReaderPredicate => ({
  field,
  op,
  value,
});

describe('predicatesToWhere', () => {
  it('空谓词 → null', () => {
    expect(predicatesToWhere([])).toBeNull();
    expect(predicatesToWhere([p('f', 'eq', '')])).toBeNull();
  });

  it('单谓词 → leaf AST', () => {
    expect(predicatesToWhere([p('students', 'gte', 400)])).toEqual({
      field: 'students',
      op: 'gte',
      value: 400,
    });
  });

  it('多谓词 → and 组合', () => {
    const where = predicatesToWhere([p('students', 'gte', 400), p('level', 'eq', 'primary')]);
    expect(where).toEqual({
      op: 'and',
      terms: [
        { field: 'students', op: 'gte', value: 400 },
        { field: 'level', op: 'eq', value: 'primary' },
      ],
    });
  });
});

describe('predicatesToMapLibreFilter', () => {
  it('空谓词 → null（不过滤）', () => {
    expect(predicatesToMapLibreFilter([])).toBeNull();
  });

  it('单谓词 → 单表达式', () => {
    expect(predicatesToMapLibreFilter([p('students', 'gte', 400)])).toEqual([
      '>=', ['get', 'students'], 400,
    ]);
  });

  it('多谓词 → all 组合', () => {
    const filter = predicatesToMapLibreFilter([p('a', 'eq', 1), p('b', 'lt', 5)]);
    expect(filter).toEqual(['all', ['==', ['get', 'a'], 1], ['<', ['get', 'b'], 5]]);
  });

  it('contains → index-of 表达式', () => {
    expect(predicatesToMapLibreFilter([p('name', 'contains', '小学')])).toEqual([
      '>=', ['index-of', ['to-string', '小学'], ['get', 'name']], 0,
    ]);
  });
});

describe('predicatesWithinWhitelist', () => {
  it('白名单内通过', () => {
    expect(predicatesWithinWhitelist([p('students', 'gte', 1)], ['students', 'level'])).toBe(true);
  });
  it('白名单外拒绝（布尔推断防线）', () => {
    expect(predicatesWithinWhitelist([p('internal_budget', 'eq', 'x')], ['students'])).toBe(false);
  });
});

describe('describePredicates', () => {
  it('人可读描述（aria 播报）', () => {
    expect(describePredicates([])).toContain('未启用筛选');
    expect(describePredicates([p('students', 'gte', 400)])).toContain('students 不小于 400');
  });
});
