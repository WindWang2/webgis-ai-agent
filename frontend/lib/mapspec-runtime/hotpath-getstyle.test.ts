import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MapSpecRuntime } from './runtime';
import { collectCartographicRuntimeObservation } from './runtime-evidence';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import type { Layer } from '@/lib/types/layer';
import { consumeDiffLastFailed, _resetWorkerBridgeForTests } from '@/lib/mapspec-compiler/worker-bridge';

/**
 * #462: map.getStyle() deep-clones the full style (MapLibre Style.serialize →
 * per-layer clone incl. paint/layout expressions) — multi-millisecond at
 * 50-100 expression-heavy sublayers. It must NOT be called on the hot paths:
 * per reconcile (syncLayerZOrder), per removed source (removeSourceSafe), or
 * per observed candidate layer (cartographic observation). Only cheap id
 * queries / maintained registries belong there; a single cold seeding call
 * when a map is first seen is the accepted budget.
 */

function makeMockMap() {
  const sources: Record<string, any> = {};
  const layers: any[] = [];
  const map: any = {
    isStyleLoaded: () => true,
    getStyle: () => ({ sources, layers }),
    getSource(id: string) { return sources[id]; },
    getLayer(id: string) { return layers.find((l) => l.id === id); },
    addSource(id: string, def: any) { sources[id] = { ...def, setData: vi.fn() }; },
    removeSource(id: string) { delete sources[id]; },
    addLayer(def: any) { layers.push(def); },
    removeLayer(id: string) {
      const i = layers.findIndex((l) => l.id === id);
      if (i >= 0) layers.splice(i, 1);
    },
    moveLayer(id: string) {
      const i = layers.findIndex((l) => l.id === id);
      if (i >= 0) {
        const [moved] = layers.splice(i, 1);
        layers.push(moved);
      }
    },
    getPaintProperty: () => 1,
    getLayoutProperty: () => 'visible',
    getCenter: () => ({ lng: 116.4, lat: 39.9 }),
    getZoom: () => 10,
    getBounds: () => ({
      getWest: () => 116.3, getSouth: () => 39.8, getEast: () => 116.5, getNorth: () => 40,
    }),
    on() {}, off() {},
    _sources: sources,
  };
  return map;
}

/** N-source spec, one circle sublayer per source (the runtime's id scheme). */
function manyLayerSpec(n: number): MapSpec {
  const sources: MapSpec['sources'] = {};
  const layers: MapSpec['layers'] = [];
  for (let i = 0; i < n; i++) {
    const id = `L${i}`;
    sources[id] = { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } };
    layers.push({ id: `${id}__point`, source: id, type: 'circle', paint: { 'circle-radius': 6 } });
  }
  return { version: '1.0', sources, layers };
}

/** Pre-existing style content (e.g. a 500-layer basemap+overlay stack). */
function seedHeavyStyle(map: any, n: number) {
  for (let i = 0; i < n; i++) {
    map.addSource(`base-src-${i}`, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    // Expression-heavy paint like production interpolate/step sublayers.
    map.addLayer({
      id: `base-${i}__fill`,
      type: 'fill',
      source: `base-src-${i}`,
      paint: {
        'fill-color': [
          'interpolate', ['linear'], ['get', 'v'],
          0, '#fff', 10, '#eee', 20, '#ddd', 30, '#ccc', 40, '#bbb', 50, '#aaa',
          60, '#999', 70, '#888', 80, '#777', 90, '#666', 100, '#555',
        ],
      },
    });
  }
}

describe('#462 — getStyle deep-clone budget on hot paths', () => {
  let map: any;
  beforeEach(() => {
    vi.stubGlobal('Worker', undefined);
    _resetWorkerBridgeForTests();
    consumeDiffLastFailed();
    map = makeMockMap();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('layer-changing reconciles + source removals on a 500-layer map: zero getStyle calls after the cold seed', async () => {
    seedHeavyStyle(map, 250); // 250 sources × 1 layer = 250 style layers
    const rt = new MapSpecRuntime(map);
    const specA = manyLayerSpec(40);
    const p0 = rt.reconcileAsync(specA);
    rt.flush();
    await p0; // registry cold-seeded here (first syncLayerZOrder) — budget spent

    const spy = vi.spyOn(map, 'getStyle');

    // Layer-changing reconcile #1: paint edit on 20 layers (recompile).
    const specB: MapSpec = {
      version: '1.0',
      sources: specA.sources,
      layers: specA.layers.map((l, i) =>
        i % 2 === 0 ? { ...l, paint: { 'circle-radius': 9 } } : l,
      ),
    };
    const p1 = rt.reconcileAsync(specB);
    rt.flush();
    await p1;

    // Reconcile #2 with 10 source removals (straggler scrub path per source).
    const removedSources = Object.keys(specB.sources).slice(0, 10);
    const specC: MapSpec = {
      version: '1.0',
      sources: Object.fromEntries(
        Object.entries(specB.sources).filter(([id]) => !removedSources.includes(id)),
      ),
      layers: specB.layers.filter((l) => !removedSources.includes(l.source)),
    };
    const p2 = rt.reconcileAsync(specC);
    rt.flush();
    await p2;

    expect(specC.layers.length).toBeGreaterThan(0);
    expect(removedSources.length).toBe(10);
    // The hot paths ran real work (recompiles + removals)…
    expect(map._sources ? Object.keys(map._sources).length : 0).toBeGreaterThan(0);
    for (const id of removedSources) expect(map.getSource(id)).toBeUndefined();
    // …without a single style deep-clone.
    expect(spy).not.toHaveBeenCalled();
  });

  it('cartographic observation over many candidate layers clones the style at most once', () => {
    seedHeavyStyle(map, 10);
    const rt = new MapSpecRuntime(map);
    const spec = manyLayerSpec(20);
    rt.reconcile(spec);
    // Warm any registry first so only the observation path is measured.
    vi.spyOn(map, 'getStyle').mockClear();

    const hudLayers: Layer[] = Array.from({ length: 20 }, (_, i) => ({
      id: `L${i}`,
      name: `Layer ${i}`,
      type: 'vector',
      visible: true,
      opacity: 1,
      group: 'analysis',
      // One spec layer fans out into several candidates; every candidate used
      // to trigger its own getStyle() clone at runtime-evidence.ts.
      _mapspecFingerprint: 'fp',
    })) as Layer[];

    const observation = collectCartographicRuntimeObservation(
      map,
      spec,
      hudLayers,
      'fp',
      '',
      rt.getAppliedSpec(),
    );

    expect((observation.layers as unknown[]).length).toBe(20);
    expect(map.getStyle.mock.calls.length).toBeLessThanOrEqual(1); // was 20+
  });
});
