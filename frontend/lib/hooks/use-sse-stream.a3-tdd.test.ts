import { describe, it, expect } from 'vitest';
import { buildSelectedFeatureSnapshot } from './use-sse-stream';
import type { SelectedFeatureInfo } from '@/lib/store/hud-types';

describe('A3 TDD — canonical is_approximate flag on wire', () => {
  const base: SelectedFeatureInfo = {
    layerId: 'ref:abc__point',
    point: [116.4, 39.9],
    properties: { name: 'x' },
    selectedAt: 1,
    isApproximate: true,
  };
  it('emits only is_approximate (snake_case), not camelCase', () => {
    const snap = buildSelectedFeatureSnapshot(base, ['ref:abc']) as any;
    expect(snap.is_approximate).toBe(true);
    // must NOT emit the camelCase duplicate on the wire
    expect(snap.isApproximate).toBeUndefined();
  });
  it('when not approximate, omits is_approximate entirely (undefined)', () => {
    const sel: SelectedFeatureInfo = { ...base, isApproximate: false };
    const snap = buildSelectedFeatureSnapshot(sel, ['ref:abc']) as any;
    expect(snap.is_approximate).toBeUndefined();
    expect(snap.isApproximate).toBeUndefined();
  });
});
