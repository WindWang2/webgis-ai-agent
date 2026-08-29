import { describe, it, expect, beforeEach } from 'vitest';
import {
  rememberCustomOverlay,
  forgetCustomOverlay,
  remountCustomOverlays,
  resetCustomOverlayRegistry,
  rememberedCustomOverlayCount,
} from './custom-overlay-registry';

describe('#1078(G-1) custom overlay remount registry', () => {
  beforeEach(() => resetCustomOverlayRegistry());

  it('remounts overlays missing from the map (basemap setStyle wipe)', () => {
    const added: string[] = [];
    const map = {
      getLayer: (id: string) => (id === 'custom-kept' ? { id } : undefined),
    };
    rememberCustomOverlay('custom-kept', () => { added.push('kept'); });
    rememberCustomOverlay('custom-wiped', () => { added.push('wiped'); });
    remountCustomOverlays(map);
    // kept 层仍存在 → 不重复挂载；wiped 层缺失 → 重挂。
    expect(added).toEqual(['wiped']);
  });

  it('forgot overlays are not remounted', () => {
    const added: string[] = [];
    const map = { getLayer: () => undefined };
    rememberCustomOverlay('custom-x', () => { added.push('x'); });
    forgetCustomOverlay('custom-x');
    remountCustomOverlays(map);
    expect(added).toEqual([]);
    expect(rememberedCustomOverlayCount()).toBe(0);
  });

  it('a failing remount does not block the remaining entries', () => {
    const added: string[] = [];
    const map = { getLayer: () => undefined };
    rememberCustomOverlay('custom-bad', () => { throw new Error('boom'); });
    rememberCustomOverlay('custom-good', () => { added.push('good'); });
    remountCustomOverlays(map);
    expect(added).toEqual(['good']);
  });

  it('registry is bounded (oldest evicted beyond 64)', () => {
    for (let i = 0; i < 70; i++) rememberCustomOverlay(`custom-${i}`, () => {});
    expect(rememberedCustomOverlayCount()).toBe(64);
  });
});
