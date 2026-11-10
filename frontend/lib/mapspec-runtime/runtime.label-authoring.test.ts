/**
 * G16（v2.1）：runtime 标注 authoring 投影（auto/pinned/callout 子层）。
 * House style：hand-rolled stub map（同 runtime.label.test.ts）。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { MapSpecRuntime } from './runtime';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
const FEATURES = [
  { type: 'Feature', id: 's1', geometry: { type: 'Point', coordinates: [116.44, 39.96] }, properties: { name: '第一小学' } },
  { type: 'Feature', id: 's2', geometry: { type: 'Point', coordinates: [116.46, 39.96] }, properties: { name: '第二小学' } },
  { type: 'Feature', id: 's3', geometry: { type: 'Point', coordinates: [116.45, 39.94] }, properties: { name: '第三小学' } },
];

function specWith(authoring: unknown): MapSpec {
  return {
    version: '1.1',
    view: { center: [116.45, 39.95], zoom: 11 },
    sources: {
      schools: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: FEATURES } } as never,
    },
    layers: [{
      id: 'schools', type: 'circle', source: 'schools',
      label: { field: 'name', ...(authoring ? { authoring } : {}) } as never,
      paint: { 'circle-radius': 4 },
    }],
  } as never;
}

function makeMockMap(opts: { zoom?: number; backgroundColor?: string } = {}) {
  const sources: Record<string, any> = {};
  const layers: any[] = [];
  const listeners = new Map<string, Set<() => void>>();
  const calls = {
    addSource: [] as Array<{ id: string; def: any }>,
    removeSource: [] as string[],
    addLayer: [] as Array<{ def: any }>,
    removeLayer: [] as string[],
    setFilter: [] as Array<{ id: string; filter: any }>,
    moveLayer: [] as string[],
    setData: [] as Array<{ id: string; data: any }>,
  };
  let zoom = opts.zoom ?? 10;
  const backgroundLayer = {
    id: "bg", type: "background",
    paint: { "background-color": opts.backgroundColor ?? "#ffffff" },
  };
  const map: any = {
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [backgroundLayer, ...layers] }),
    getZoom: () => zoom,
    setZoom: (z: number) => { zoom = z; },
    getSource(id: string) { return sources[id]; },
    getLayer(id: string) { return layers.find((l) => l.id === id); },
    addSource(id: string, def: any) {
      sources[id] = { ...def, setData: vi.fn() };
      calls.addSource.push({ id, def });
    },
    removeSource(id: string) { delete sources[id]; calls.removeSource.push(id); },
    addLayer(def: any) {
      if (layers.some((l) => l.id === def.id)) {
        throw new Error(`Layer with id ${def.id} already exists on this map`);
      }
      layers.push(def);
      calls.addLayer.push({ def });
    },
    removeLayer(id: string) {
      const i = layers.findIndex((l) => l.id === id);
      if (i >= 0) layers.splice(i, 1);
      calls.removeLayer.push(id);
    },
    setFilter(id: string, filter: any) {
      const l = layers.find((x) => x.id === id);
      if (l) l.filter = filter;
      calls.setFilter.push({ id, filter });
    },
    moveLayer(id: string) { calls.moveLayer.push(id); },
    on(event: string, handler: () => void) {
      const handlers = listeners.get(event) ?? new Set();
      handlers.add(handler);
      listeners.set(event, handlers);
    },
    off(event: string, handler: () => void) {
      listeners.get(event)?.delete(handler);
    },
    _emit(event: string) {
      for (const handler of Array.from(listeners.get(event) ?? [])) handler();
    },
    _sources: sources,
    _layers: layers,
    _calls: calls,
    _listenerCount(event: string) { return listeners.get(event)?.size ?? 0; },
  };
  return map;
}

describe('runtime label authoring（G16）', () => {
  let map: ReturnType<typeof makeMockMap>;
  let runtime: MapSpecRuntime;

  beforeEach(() => {
    map = makeMockMap({ zoom: 11 });
    runtime = new MapSpecRuntime(map as never);
  });

  it('无 authoring：不建 pinned/callout 子层（additive）', () => {
    runtime.reconcile(specWith(undefined));
    expect(map.getLayer('schools-label')).toBeTruthy();
    expect(map.getLayer('schools-label-pinned')).toBeFalsy();
    expect(map.getLayer('schools-label-callout')).toBeFalsy();
  });

  it('pinned 覆写 → pinned 子层（allow-overlap + offset）+ auto 子层排除', () => {
    runtime.reconcile(specWith({
      overrides: [
        { featureId: 's1', pinned: true, offset: { dx: 1.5, dy: -1 }, idKey: null },
      ],
    }));
    const pinned = map.getLayer('schools-label-pinned') as any;
    expect(pinned).toBeTruthy();
    expect(pinned.layout['text-allow-overlap']).toBe(true);
    expect(JSON.stringify(pinned.layout['text-offset'])).toContain('1.5');
    const auto = map.getLayer('schools-label') as any;
    // auto 子层排除 pinned（match false 分支在场）。
    expect(JSON.stringify(auto.filter)).toContain('s1');
  });

  it('hidden 覆写 → auto 子层 filter 排除（live 不显示）', () => {
    runtime.reconcile(specWith({
      overrides: [{ featureId: 's2', hidden: true, idKey: null }],
    }));
    const auto = map.getLayer('schools-label') as any;
    expect(JSON.stringify(auto.filter)).toContain('s2');
    expect(map.getLayer('schools-label-pinned')).toBeFalsy();
  });

  it('text 覆写 → match 表达式 text-field', () => {
    runtime.reconcile(specWith({
      overrides: [{ featureId: 's1', text: '市重点', idKey: null }],
    }));
    const auto = map.getLayer('schools-label') as any;
    const tf = JSON.stringify(auto.layout['text-field']);
    expect(tf).toContain('市重点');
    expect(tf).toContain('match');
  });

  it('callout 覆写 → callout 线层 + 合成源', () => {
    runtime.reconcile(specWith({
      overrides: [{
        featureId: 's1', pinned: true, callout: true,
        offset: { dx: 2, dy: 2 }, idKey: null,
      }],
    }));
    expect(map.getLayer('schools-label-callout')).toBeTruthy();
    expect(map.getSource('schools-label-callout-src')).toBeTruthy();
  });

  it('orphan 覆写不投影 + 事件披露', () => {
    runtime.reconcile(specWith({
      overrides: [{ featureId: 'ghost', pinned: true, offset: { dx: 1, dy: 1 }, idKey: null }],
    }));
    // orphan → 无 pinned 子层；事件携带 dropped_orphan。
    expect(map.getLayer('schools-label-pinned')).toBeFalsy();
    const events = runtime.getLabelEvents();
    const orphanEv = events.find((e) => e.kind === 'authoring'
      && (e.detail as Record<string, unknown>).dropped_orphan);
    expect(orphanEv).toBeTruthy();
    expect((orphanEv!.detail as { dropped_orphan: string[] }).dropped_orphan).toContain('ghost');
  });

  it('覆写清空 → pinned/callout 子层卸载（不留 ghost）', () => {
    runtime.reconcile(specWith({
      overrides: [{ featureId: 's1', pinned: true, callout: true, offset: { dx: 2, dy: 2 }, idKey: null }],
    }));
    expect(map.getLayer('schools-label-pinned')).toBeTruthy();
    runtime.reconcile(specWith(undefined));
    expect(map.getLayer('schools-label-pinned')).toBeFalsy();
    expect(map.getLayer('schools-label-callout')).toBeFalsy();
  });

  it('主层删除 → authoring 子层同生命周期卸载', () => {
    runtime.reconcile(specWith({
      overrides: [{ featureId: 's1', pinned: true, offset: { dx: 1, dy: 1 }, idKey: null }],
    }));
    expect(map.getLayer('schools-label-pinned')).toBeTruthy();
    const noLayer = specWith({ overrides: [] });
    (noLayer as { layers: unknown[] }).layers = [];
    runtime.reconcile(noLayer);
    expect(map.getLayer('schools-label-pinned')).toBeFalsy();
  });

  it('label-only 变更（加覆写）不重建主层', () => {
    runtime.reconcile(specWith(undefined));
    const mainAddsBefore = map._calls.addLayer.filter((c) => c.def.id === 'schools').length;
    runtime.reconcile(specWith({
      overrides: [{ featureId: 's1', pinned: true, offset: { dx: 1, dy: 1 }, idKey: null }],
    }));
    const mainAddsAfter = map._calls.addLayer.filter((c) => c.def.id === 'schools').length;
    expect(mainAddsAfter).toBe(mainAddsBefore);
    expect(map.getLayer('schools-label-pinned')).toBeTruthy();
  });
});
