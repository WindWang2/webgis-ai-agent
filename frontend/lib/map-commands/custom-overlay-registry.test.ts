import { describe, it, expect, beforeEach } from 'vitest';
import {
  rememberCustomOverlay,
  forgetCustomOverlay,
  remountCustomOverlays,
  resetCustomOverlayRegistry,
  rememberedCustomOverlayCount,
} from './custom-overlay-registry';
import { describeRuntimeLayers } from '../map-kit/runtime-layer-registry';

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

  it('registry is bounded (FIFO eviction past the unified cap)', () => {
    // v3(Phase B)：双账本收敛进 runtime-layer-registry —— 统一界 256
    // （v2 闭包账本为 64、定义账本无界；统一界须同时覆盖两者语义）。
    for (let i = 0; i < 300; i++) rememberCustomOverlay(`custom-${i}`, () => {});
    expect(rememberedCustomOverlayCount()).toBe(256);
    // FIFO：最早登记的 custom-0 已被驱逐，custom-299 仍在。
    const ids = describeRuntimeLayers().map((e) => e.runtimeLayerId);
    expect(ids).not.toContain('custom-0');
    expect(ids).toContain('custom-299');
  });
});
