/**
 * G25 where-ast 契约测试 —— 与后端 feature_pages.evaluate_where 语义逐位
 * 对齐（移植验收面：缺席语义、bool/数值同型、字符串字典序窗口、大小写）。
 */
import { describe, expect, it } from 'vitest';

import {
  evaluateWhereAst,
  filterFeaturesByWhere,
  WhereAstError,
  type WhereNode,
} from '@/lib/cartography/where-ast';

const f = (props: Record<string, unknown>) => ({ properties: props });

describe('evaluateWhereAst（后端同源语义）', () => {
  it('eq/ne：null 目标命中缺席；数值跨 int/float 相等；bool 不与数值相等', () => {
    expect(evaluateWhereAst({ op: 'eq', field: 'a', value: null }, f({}))).toBe(true);
    expect(evaluateWhereAst({ op: 'eq', field: 'a', value: null }, f({ a: null }))).toBe(true);
    expect(evaluateWhereAst({ op: 'eq', field: 'a', value: null }, f({ a: 0 }))).toBe(false);
    expect(evaluateWhereAst({ op: 'eq', field: 'a', value: 1 }, f({ a: 1.0 }))).toBe(true);
    expect(evaluateWhereAst({ op: 'eq', field: 'a', value: 1 }, f({ a: true }))).toBe(false);
    expect(evaluateWhereAst({ op: 'eq', field: 'a', value: '1' }, f({ a: 1 }))).toBe(false);
    expect(evaluateWhereAst({ op: 'ne', field: 'a', value: null }, f({}))).toBe(false);
    expect(evaluateWhereAst({ op: 'ne', field: 'a', value: 2 }, f({}))).toBe(true); // 缺席 ≠ 2
  });

  it('in：多值命中与缺席语义', () => {
    const w: WhereNode = { op: 'in', field: 'year', values: [2020, 2022] };
    expect(evaluateWhereAst(w, f({ year: 2020 }))).toBe(true);
    expect(evaluateWhereAst(w, f({ year: 2021 }))).toBe(false);
    expect(evaluateWhereAst(w, f({}))).toBe(false);
    const withNull: WhereNode = { op: 'in', field: 'year', values: [null, 2024] };
    expect(evaluateWhereAst(withNull, f({}))).toBe(true);
  });

  it('between：字符串字典序窗口（定宽时间编码）；数值窗口排除 bool/非有限', () => {
    const strW: WhereNode = { op: 'between', field: 'ym', gte: '2022-01', lte: '2024-06' };
    expect(evaluateWhereAst(strW, f({ ym: '2023-07' }))).toBe(true);
    expect(evaluateWhereAst(strW, f({ ym: '2025-01' }))).toBe(false);
    expect(evaluateWhereAst(strW, f({ ym: 2023 }))).toBe(false); // 非字符串不命中
    const numW: WhereNode = { op: 'between', field: 'v', gte: 1, lte: 10 };
    expect(evaluateWhereAst(numW, f({ v: 5 }))).toBe(true);
    expect(evaluateWhereAst(numW, f({ v: true }))).toBe(false);
    expect(evaluateWhereAst(numW, f({ v: '5' }))).toBe(false);
  });

  it('范围单界：字符串界仅字符串字段；数值界排除 bool/非有限', () => {
    const strGte: WhereNode = { op: 'gte', field: 'ym', value: '2023-01' };
    expect(evaluateWhereAst(strGte, f({ ym: '2023-02' }))).toBe(true);
    expect(evaluateWhereAst(strGte, f({ ym: 20230102 }))).toBe(false);
    const numLt: WhereNode = { op: 'lt', field: 'v', value: 100 };
    expect(evaluateWhereAst(numLt, f({ v: 99 }))).toBe(true);
    expect(evaluateWhereAst(numLt, f({ v: false }))).toBe(false);
    expect(evaluateWhereAst(numLt, f({ v: NaN }))).toBe(false);
  });

  it('contains/startswith 大小写不敏感，仅字符串字段', () => {
    const c: WhereNode = { op: 'contains', field: 'name', value: 'Chengdu' };
    expect(evaluateWhereAst(c, f({ name: 'chengdu city' }))).toBe(true);
    expect(evaluateWhereAst(c, f({ name: 'Beijing' }))).toBe(false);
    expect(evaluateWhereAst(c, f({ name: 7 }))).toBe(false);
    const s: WhereNode = { op: 'startswith', field: 'code', value: 'CD-' };
    expect(evaluateWhereAst(s, f({ code: 'cd-01' }))).toBe(true);
    expect(evaluateWhereAst(s, f({ code: 'BJ-01' }))).toBe(false);
  });

  it('组合算子 and/or/not', () => {
    const w: WhereNode = {
      op: 'and',
      terms: [
        { op: 'gte', field: 'v', value: 10 },
        { op: 'or', terms: [{ op: 'eq', field: 'cat', value: 'a' }, { op: 'eq', field: 'cat', value: 'b' }] },
        { op: 'not', term: { op: 'eq', field: 'flag', value: true } },
      ],
    };
    expect(evaluateWhereAst(w, f({ v: 20, cat: 'b', flag: false }))).toBe(true);
    expect(evaluateWhereAst(w, f({ v: 20, cat: 'c', flag: false }))).toBe(false);
    expect(evaluateWhereAst(w, f({ v: 20, cat: 'a', flag: true }))).toBe(false);
  });

  it('未知算子/畸形节点 typed 抛错（不静默放行）', () => {
    expect(() =>
      evaluateWhereAst({ op: 'regex', field: 'a', value: 1 } as unknown as WhereNode, f({ a: 1 })),
    ).toThrow(WhereAstError);
    expect(() => evaluateWhereAst({ op: 'not' } as unknown as WhereNode, f({}))).toThrow(WhereAstError);
  });

  it('filterFeaturesByWhere：null 原样；过滤保序', () => {
    const feats = [f({ y: 2020 }), f({ y: 2021 }), f({ y: 2022 }), f({})];
    const out = filterFeaturesByWhere(feats, { op: 'in', field: 'y', values: [2020, 2022] });
    expect(out).toHaveLength(2);
    expect((out[0].properties as { y: number }).y).toBe(2020);
    expect(filterFeaturesByWhere(feats, null)).toHaveLength(4);
  });
});
