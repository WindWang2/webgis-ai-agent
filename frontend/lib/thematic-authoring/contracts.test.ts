/**
 * T-F1：TS 孪生求值器与 Python 端逐点一致（共享 fixture 双端断言）。
 * fixture 由 Python（stats.assign_class / builder.size_for_value）生成：
 * frontend/lib/thematic-authoring/twins.fixture.json；Python 侧对账在
 * tests/unit/thematic_authoring/test_stats_breaks.py::test_twin_fixture_parity。
 */
import { describe, expect, it } from 'vitest';
import { assignClass, sizeForValue } from './contracts';
import fixture from './twins.fixture.json';

describe('assignClass twin parity (Python fixture)', () => {
  it('matches every fixture case', () => {
    const cases = fixture.class_cases as Array<{
      value: number; breaks: number[]; expected: number;
    }>;
    expect(cases.length).toBeGreaterThan(80);
    for (const c of cases) {
      expect(assignClass(c.value, c.breaks), `${c.value} in ${JSON.stringify(c.breaks)}`)
        .toBe(c.expected);
    }
  });

  it('step semantics: last break is a legend bound, not a threshold', () => {
    expect(assignClass(50, [0, 10, 20, 50])).toBe(2);
    expect(assignClass(1e18, [0, 10, 20, 50])).toBe(2); // over-domain → last class
    expect(assignClass(-5, [0, 10, 20, 50])).toBe(0);   // under-domain → first class
    expect(assignClass(10, [0, 10, 20, 50])).toBe(1);   // on-break → upper class
  });
});

describe('sizeForValue twin parity (Python fixture)', () => {
  it('matches every fixture case (incl. clamping)', () => {
    const cases = fixture.size_cases as Array<{
      channel: {
        min_value: number; max_value: number; min_size: number;
        max_size: number; scaling: string;
      }; value: number; expected: number;
    }>;
    for (const c of cases) {
      const got = sizeForValue(c.value, c.channel);
      expect(got, `${c.value} ${JSON.stringify(c.channel)}`).toBeCloseTo(c.expected, 9);
    }
  });

  it('area_sqrt keeps AREA linear in value (exact, not piecewise approx)', () => {
    const ch = { min_value: 100, max_value: 1600, min_size: 4, max_size: 10, scaling: 'area_sqrt' };
    const area = (v: number) => (sizeForValue(v, ch) as number) ** 2;
    const a0 = area(100);
    const a1 = area(1600);
    expect(area(475)).toBeCloseTo(a0 + (a1 - a0) * 0.25, 9);
    expect(area(850)).toBeCloseTo(a0 + (a1 - a0) * 0.5, 9);
  });

  it('returns null for non-finite input', () => {
    const ch = { min_value: 0, max_value: 10, min_size: 1, max_size: 5, scaling: 'linear' };
    expect(sizeForValue(NaN, ch)).toBeNull();
    expect(sizeForValue(Infinity, ch)).toBeNull();
  });
});
