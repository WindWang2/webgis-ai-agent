/**
 * LayerFilterEvidence（§14）契约：封闭算子求值 / 状态推导 / 有界扫描。
 */
import { describe, expect, it, beforeEach } from 'vitest';
import {
  clearFilterEvidence,
  deriveFilterEvidence,
  evaluateFilterBounded,
  FILTER_SCAN_LIMIT,
  getFilterEvidence,
  recordFilterEvidence,
  collectFilterFields,
} from './filter-evidence';

const features = [
  { properties: { district: '武侯区', count: 10 }, geometry: { type: 'Point' } },
  { properties: { district: '锦江区', count: 20 }, geometry: { type: 'Point' } },
  { properties: { district: '青羊区', count: 30 }, geometry: { type: 'Point' } },
];

describe('evaluateFilterBounded (closed operator vocabulary)', () => {
  it('evaluates adapter-emitted expressions', () => {
    const inExpr = ['in', ['get', 'district'], ['literal', ['武侯区', '锦江区']]];
    expect(evaluateFilterBounded(inExpr, features[0])).toBe(true);
    expect(evaluateFilterBounded(inExpr, features[2])).toBe(false);
    const rangeExpr = ['all',
      ['==', '$type', 'Point'],
      ['>=', ['get', 'count'], 20],
      ['<', ['get', 'count'], 30]];
    expect(evaluateFilterBounded(rangeExpr, features[0])).toBe(false);
    expect(evaluateFilterBounded(rangeExpr, features[1])).toBe(true);
  });

  it('unknown operator → null (honest unknown, never guess)', () => {
    expect(evaluateFilterBounded(['within', ['get', 'x']], features[0])).toBeNull();
    expect(evaluateFilterBounded(['any', ['within', ['get', 'x']], ['==', ['get', 'district'], '武侯区']], features[0])).toBe(true);
  });

  // #1726 regression: the real selection filter is
  // ['in', ['to-string', ['get', f]], ['literal', [...]]]
  // (selection-store.getSelectionFilterExpression). getValue did not know
  // `to-string`, so the operand resolved to undefined while
  // _isUnresolvedOperand still called it "recognized" → the `in` branch
  // returned a hard false for every feature → a fully rendered layer was
  // reported as "过滤后 0 要素".
  it('evaluates to-string-wrapped operands (selection filter shape)', () => {
    const selExpr = ['in', ['to-string', ['get', 'district']], ['literal', ['武侯区', '锦江区']]];
    expect(evaluateFilterBounded(selExpr, features[0])).toBe(true);
    expect(evaluateFilterBounded(selExpr, features[1])).toBe(true);
    expect(evaluateFilterBounded(selExpr, features[2])).toBe(false);
  });

  it('to-string normalizes numeric properties for string haystacks', () => {
    const numeric = { properties: { OBJECTID: 42 }, geometry: { type: 'Point' } };
    const idExpr = ['in', ['to-string', ['get', 'OBJECTID']], ['literal', ['42']]];
    expect(evaluateFilterBounded(idExpr, numeric)).toBe(true);
    // A missing property is "" (MapLibre to-string semantics), not undefined.
    expect(evaluateFilterBounded(['in', ['to-string', ['get', 'absent']], ['literal', ['']]], numeric)).toBe(true);
  });

  it('to-string over an unrecognized operand stays unknown (no fabricated "")', () => {
    expect(evaluateFilterBounded(['in', ['to-string', ['geometry-type']], ['literal', ['Point']]], features[0])).toBeNull();
  });
});

describe('deriveFilterEvidence', () => {
  it('inactive without filters', () => {
    expect(deriveFilterEvidence({ layerId: 'l', sublayerFilters: [], features }).status).toBe('inactive');
  });

  it('empty when the filter matches nothing', () => {
    const evidence = deriveFilterEvidence({
      layerId: 'l',
      sublayerFilters: [['in', ['get', 'district'], ['literal', ['不存在区']]]],
      features,
    });
    expect(evidence.status).toBe('empty');
    expect(evidence.matched_count).toBe(0);
  });

  it('active with matched count', () => {
    const evidence = deriveFilterEvidence({
      layerId: 'l',
      sublayerFilters: [['in', ['get', 'district'], ['literal', ['武侯区']]]],
      features,
    });
    expect(evidence.status).toBe('active');
    expect(evidence.matched_count).toBe(1);
  });

  it('invalid when the field does not exist on any sampled feature', () => {
    const evidence = deriveFilterEvidence({
      layerId: 'l',
      sublayerFilters: [['in', ['get', 'wrong_field'], ['literal', ['x']]]],
      features,
    });
    expect(evidence.status).toBe('invalid');
  });

  it('unknown for MVT/tile layers (no inline features)', () => {
    const evidence = deriveFilterEvidence({
      layerId: 'l',
      sublayerFilters: [['==', '$type', 'Point']],
      features: undefined,
    });
    expect(evidence.status).toBe('unknown');
  });

  it('unknown above the scan limit (no 100k scans for a badge)', () => {
    const big = Array.from({ length: FILTER_SCAN_LIMIT + 1 }, (_, i) => ({
      properties: { v: i }, geometry: { type: 'Point' },
    }));
    const evidence = deriveFilterEvidence({
      layerId: 'l',
      sublayerFilters: [['==', '$type', 'Point']],
      features: big,
    });
    expect(evidence.status).toBe('unknown');
    expect(evidence.scanned).toBe(big.length);
  });

  it('unknown when unknown operators dominate an otherwise-empty result', () => {
    // 字段在场（invalid 检查通过）但算子不可求值 → 如实 unknown。
    const evidence = deriveFilterEvidence({
      layerId: 'l',
      sublayerFilters: [['custom-op', ['get', 'district']]],
      features,
    });
    expect(evidence.status).toBe('unknown');
  });

  // #1726 regression: a chart/legend-linked layer carries the selection filter
  // verbatim; the bounded evaluator used to score it "命中 0" and the Layers
  // panel raised a "过滤后 0 要素" warning badge over a layer that renders.
  it('active (not empty) for the selection-store category filter', () => {
    const evidence = deriveFilterEvidence({
      layerId: 'l',
      sublayerFilters: [['in', ['to-string', ['get', 'district']], ['literal', ['武侯区', '锦江区']]]],
      features,
    });
    expect(evidence.status).toBe('active');
    expect(evidence.matched_count).toBe(2);
  });

  it('invalid wins over unknown operator when the field is absent (operator-independent evidence)', () => {
    const evidence = deriveFilterEvidence({
      layerId: 'l',
      sublayerFilters: [['custom-op', ['get', 'nope']]],
      features,
    });
    expect(evidence.status).toBe('invalid');
  });
});

describe('module store', () => {
  beforeEach(() => clearFilterEvidence());

  it('records and reads latest-wins evidence', () => {
    recordFilterEvidence([
      { layerId: 'a', evidence: { status: 'active', matched_count: 3, at: 1 } },
      { layerId: 'b', evidence: { status: 'empty', at: 1 } },
    ]);
    expect(getFilterEvidence('a')?.status).toBe('active');
    expect(getFilterEvidence('b')?.status).toBe('empty');
    expect(getFilterEvidence('c')).toBeNull();
  });

  it('collectFilterFields gathers referenced fields', () => {
    const fields = collectFilterFields(['all', ['==', ['get', 'a'], 1], ['in', ['get', 'b'], ['literal', [1]]]]);
    expect([...fields].sort()).toEqual(['a', 'b']);
  });
});
