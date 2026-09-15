import { describe, expect, it } from 'vitest';

import type { MapSpec } from '@/lib/mapspec-compiler/types';
import { scenarioModeOf, scenarioModeToComparisonKind } from '@/lib/mapspec/scenario-mode';

function specWith(mode?: string | null): MapSpec {
  return {
    version: '1.3',
    scenario_mode: mode,
    sources: {},
    layers: [],
  } as unknown as MapSpec;
}

describe('scenarioModeOf', () => {
  it('split_view / swipe_compare 原样读出', () => {
    expect(scenarioModeOf(specWith('split_view'))).toBe('split_view');
    expect(scenarioModeOf(specWith('swipe_compare'))).toBe('swipe_compare');
  });

  it('缺失 / null / 非法值 → null（非推演视图）', () => {
    expect(scenarioModeOf(specWith(undefined))).toBeNull();
    expect(scenarioModeOf(specWith(null))).toBeNull();
    expect(scenarioModeOf(specWith('hologram'))).toBeNull();
    expect(scenarioModeOf(null)).toBeNull();
    expect(scenarioModeOf(undefined)).toBeNull();
  });
});

describe('scenarioModeToComparisonKind（ADR-0193 协议映射）', () => {
  it('split_view → side-by-side（双栏对照）', () => {
    expect(scenarioModeToComparisonKind('split_view')).toBe('side-by-side');
  });

  it('swipe_compare → swipe（卷帘对比）', () => {
    expect(scenarioModeToComparisonKind('swipe_compare')).toBe('swipe');
  });

  it('null / 非法值 → null（应退出对比视图）', () => {
    expect(scenarioModeToComparisonKind(null)).toBeNull();
    expect(scenarioModeToComparisonKind(undefined)).toBeNull();
    expect(scenarioModeToComparisonKind('hologram')).toBeNull();
  });
});
