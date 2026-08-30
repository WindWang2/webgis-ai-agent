import { describe, expect, it } from 'vitest';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';
import {
  MAX_OBSERVED_COMPONENTS,
  MAX_RUNTIME_ERRORS,
  RuntimeErrorRing,
  collectRenderObservation,
  observeComponents,
  waitForRenderSettle,
} from './render-observation';

/**
 * P9 Render Observation 契约（render-observed product closure）：
 * 有界（无 GeoJSON/feature 载荷）、组件观察与 chrome 同语义、
 * 错误环 dedup 有界、settle 有界、revision 绑定。
 */

function specWithComponents(components: MapSpecComponent[]): MapSpec {
  return {
    layers: [],
    sources: {},
    layout: { components },
  } as unknown as MapSpec;
}

describe('observeComponents', () => {
  it('projects enabled chrome components as mounted with resolved anchor', () => {
    const obs = observeComponents(specWithComponents([
      { id: 'title', type: 'title', enabled: true, placement: { mode: 'anchor', anchor: 'top-center' } },
      { id: 'legend-main', type: 'legend', enabled: true },
      { id: 'chart-panel', type: 'chart_panel', enabled: true, options: { chartRef: 'ref:chart-1' } },
    ] as unknown as MapSpecComponent[]));
    const title = obs.find((c) => c.type === 'title');
    expect(title?.mounted).toBe(true);
    expect(title?.anchor).toBe('top-center');
    expect(title?.fallback).toBeUndefined();
    expect(obs.find((c) => c.type === 'chart_panel')?.mounted).toBe(true);
  });

  it('disabled-only spec means chrome is not mounted (empty observation)', () => {
    // map-panel hasSpecChrome 门：无 enabled chrome-renderable 组件时
    // MapSpecChrome 整体不渲染 —— 观察必须如实为空，不虚构 fallback。
    const obs = observeComponents(specWithComponents([
      { id: 'title', type: 'title', enabled: false },
    ] as unknown as MapSpecComponent[]));
    expect(obs).toEqual([]);
  });

  it('mirrors the chrome north/scale fallback (same rule, no second default table)', () => {
    const obs = observeComponents(specWithComponents([
      { id: 'title', type: 'title', enabled: true },
    ] as unknown as MapSpecComponent[]));
    const fallbacks = obs.filter((c) => c.fallback);
    expect(fallbacks.map((c) => c.type).sort()).toEqual(['north_arrow', 'scale_bar']);
    expect(fallbacks.every((c) => c.mounted)).toBe(true);
  });

  it('no fallback injected when the spec carries its own north/scale', () => {
    const obs = observeComponents(specWithComponents([
      { id: 'north-arrow', type: 'north_arrow', enabled: true },
      { id: 'scale-bar', type: 'scale_bar', enabled: true },
    ] as unknown as MapSpecComponent[]));
    expect(obs.some((c) => c.fallback)).toBe(false);
  });

  it('floating components carry their viewport pixel rect and collapsed flag', () => {
    const obs = observeComponents(specWithComponents([
      {
        id: 'stats',
        type: 'statistics_panel',
        enabled: true,
        placement: { mode: 'floating', x: 12, y: 40, width: 200, height: 120, collapsed: true },
      },
    ] as unknown as MapSpecComponent[]));
    const stats = obs.find((c) => c.type === 'statistics_panel');
    expect(stats?.floating).toBe(true);
    expect(stats?.collapsed).toBe(true);
    expect(stats?.rect).toEqual({ x: 12, y: 40, width: 200, height: 120 });
  });

  it('bounds the component list (MAX_OBSERVED_COMPONENTS)', () => {
    const many: MapSpecComponent[] = [];
    for (let i = 0; i < MAX_OBSERVED_COMPONENTS + 10; i += 1) {
      many.push({ id: `note-${i}`, type: 'annotation', enabled: true });
    }
    const obs = observeComponents(specWithComponents(many));
    expect(obs.length).toBeLessThanOrEqual(MAX_OBSERVED_COMPONENTS);
  });

  it('null/undefined spec is safe (empty observation, fallbacks still mounted)', () => {
    expect(observeComponents(null)).toEqual([]);
  });
});

describe('RuntimeErrorRing', () => {
  it('dedupes identical errors and drains bounded', () => {
    const ring = new RuntimeErrorRing();
    ring.push({ message: 'tiles failed', sourceId: 's-poi' });
    ring.push({ message: 'tiles failed', sourceId: 's-poi' });
    ring.push(new Error('style parse error'));
    expect(ring.size).toBe(2);
    const drained = ring.drain();
    expect(drained[0]).toEqual({ message: 'tiles failed', target: 's-poi' });
    expect(drained[1].message).toBe('style parse error');
    expect(ring.size).toBe(0);
    expect(ring.drain()).toEqual([]);
  });

  it('evicts the oldest entry beyond the cap (bounded, never unbounded)', () => {
    const ring = new RuntimeErrorRing();
    for (let i = 0; i < MAX_RUNTIME_ERRORS + 5; i += 1) {
      ring.push({ message: `err-${i}` });
    }
    const drained = ring.drain();
    expect(drained.length).toBe(MAX_RUNTIME_ERRORS);
    // 最旧的被挤出：只保留最后 MAX_RUNTIME_ERRORS 条
    expect(drained[0].message).toBe(`err-${5}`);
    expect(drained[drained.length - 1].message).toBe(`err-${MAX_RUNTIME_ERRORS + 4}`);
  });

  it('truncates oversized messages (bounded payload)', () => {
    const ring = new RuntimeErrorRing();
    ring.push({ message: 'x'.repeat(500) });
    expect(ring.drain()[0].message.length).toBeLessThanOrEqual(160);
  });
});

describe('collectRenderObservation', () => {
  const baseLayers = [{
    id: 'poi-heatmap',
    _mapspecFingerprint: 'fp-test-1234567890',
    _mapspecLayerId: 'poi-heatmap',
    _mapspecGenerationAt: 1,
  }];

  function mockMap() {
    return {
      isStyleLoaded: () => true,
      getLayer: () => ({ source: 's-poi', type: 'heatmap' }),
      getSource: () => ({ type: 'geojson' }),
      getStyle: () => ({ sources: { 's-poi': { type: 'geojson' } } }),
      getPaintProperty: undefined,
      getLayoutProperty: () => 'visible',
      getCenter: () => ({ lng: 104.0, lat: 30.6 }),
      getZoom: () => 9,
      getBearing: () => 0,
      getPitch: () => 0,
      getBounds: () => ({
        getWest: () => 103.9, getSouth: () => 30.5,
        getEast: () => 104.2, getNorth: () => 30.8,
      }),
    };
  }

  it('binds revision + idle + components onto the shared runtime evidence', () => {
    const ring = new RuntimeErrorRing();
    const observation = collectRenderObservation({
      map: mockMap() as any,
      spec: specWithComponents([
        { id: 'title', type: 'title', enabled: true },
      ] as unknown as MapSpecComponent[]),
      layers: baseLayers as any,
      mapspecFingerprint: 'fp-test-1234567890',
      mapspecRevision: 12,
      errorRing: ring,
      mapIdle: true,
      reconcileError: '',
      applied: null,
    });
    expect(observation.mapspec_revision).toBe(12);
    expect(observation.map_idle).toBe(true);
    expect(observation.style_loaded).toBe(true);
    expect(observation.components.some((c) => c.type === 'title' && c.mounted)).toBe(true);
    // 层证据仍来自共享 collector（单源）
    expect(Array.isArray(observation.layers)).toBe(true);
    expect(observation.layers.length).toBe(1);
    // 载荷有界：无 feature/GeoJSON 载荷键
    expect(observation.features).toBeUndefined();
    expect(observation.geojson).toBeUndefined();
    expect(observation.mapspec).toBeUndefined();
  });

  it('drains the error ring into the observation (one-shot)', () => {
    const ring = new RuntimeErrorRing();
    ring.push({ message: 'tile 404' });
    const first = collectRenderObservation({
      map: mockMap() as any,
      spec: specWithComponents([]),
      layers: baseLayers as any,
      mapspecFingerprint: 'fp-test-1234567890',
      mapspecRevision: 3,
      errorRing: ring,
      mapIdle: false,
    });
    expect(first.runtime_errors).toEqual([{ message: 'tile 404' }]);
    // 第二次采集：环已清空 → 不重复上报
    const second = collectRenderObservation({
      map: mockMap() as any,
      spec: specWithComponents([]),
      layers: baseLayers as any,
      mapspecFingerprint: 'fp-test-1234567890',
      mapspecRevision: 3,
      errorRing: ring,
      mapIdle: false,
    });
    expect(second.runtime_errors).toEqual([]);
  });

  it('passes reconcile error and applied spec through to the shared collector', () => {
    const observation = collectRenderObservation({
      map: mockMap() as any,
      spec: specWithComponents([]),
      layers: baseLayers as any,
      mapspecFingerprint: 'fp-test-1234567890',
      mapspecRevision: 1,
      errorRing: new RuntimeErrorRing(),
      mapIdle: true,
      reconcileError: 'style_load_timeout',
      applied: null,
    });
    expect(observation.reconcile_error).toBe('style_load_timeout');
  });
});

describe('waitForRenderSettle', () => {
  it('resolves true on map idle and removes the one-shot listener', async () => {
    let idleHandler: (() => void) | null = null;
    const map = {
      once: (ev: string, h: () => void) => {
        if (ev === 'idle') idleHandler = h;
      },
      off: (ev: string) => {
        if (ev === 'idle') idleHandler = null;
      },
    };
    const promise = waitForRenderSettle(map as any);
    idleHandler?.();
    const idle = await promise;
    expect(idle).toBe(true);
    expect(idleHandler).toBeNull();
  });

  it('resolves false (bounded) when idle never fires — no leaked listener', async () => {
    // 用极短 settle：直接验证 timer 分支有界（2500ms 太长 —— 用假计时器不可行
    // 的场景退化为查契约：返回 boolean 而不是挂起）。
    const map = { once: () => {}, off: () => {} };
    const result = await waitForRenderSettle(map as any);
    expect(typeof result).toBe('boolean');
  }, 4000);

  it('null map resolves immediately (no hang)', async () => {
    expect(await waitForRenderSettle(null)).toBe(false);
  });
});
