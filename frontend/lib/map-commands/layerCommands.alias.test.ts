import { describe, it, expect, vi, beforeEach } from 'vitest';
import { layerCommands } from './layerCommands';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';

// keep real matchMapLayers registry, mock only mutators
vi.mock('@/lib/map-kit/renderer', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/map-kit/renderer')>()),
  updateLayerStyle: vi.fn(),
  removeLayerStack: vi.fn().mockReturnValue(true),
}));

function hudWithLayer(id: string) {
  return {
    layers: [{ id, name: id }],
    baseLayer: 'OSM 地图',
    removeLayer: vi.fn(),
    setBaseLayer: vi.fn(),
    updateLayer: vi.fn(),
    reorderLayers: vi.fn(),
  };
}

describe('layerCommands #935 id alias', () => {
  beforeEach(() => vi.clearAllMocks());

  it('remove_layer accepts canonical id and still accepts legacy layer_id / layerId', () => {
    const paramsId = { id: 'target-layer-1' };
    expect(layerCommands.remove_layer.requiredParams(paramsId)).toBe(true);
    const paramsLegacy = { layer_id: 'target-layer-1' };
    expect(layerCommands.remove_layer.requiredParams(paramsLegacy)).toBe(true);
    const paramsLegacy2 = { layerId: 'target-layer-1' };
    expect(layerCommands.remove_layer.requiredParams(paramsLegacy2)).toBe(true);

    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'target-layer-1__point', type: 'circle' });
    const ctxId: any = { map, params: paramsId, getHudState: () => hudWithLayer('target-layer-1') };
    const resId = layerCommands.remove_layer.run(ctxId) as any;
    expect(resId.status).toBe('succeeded');

    const ctxLegacy: any = { map: makeMockMaplibreMap(), params: paramsLegacy, getHudState: () => hudWithLayer('target-layer-1') };
    // need map with layer for legacy path too
    ctxLegacy.map.addLayer({ id: 'target-layer-1__point', type: 'circle' });
    const resLegacy = layerCommands.remove_layer.run(ctxLegacy) as any;
    expect(resLegacy.status).toBe('succeeded');
  });

  it('base_layer_change accepts canonical id and still accepts legacy name', async () => {
    expect(layerCommands.base_layer_change.requiredParams({ id: 'OSM 地图' })).toBe(true);
    expect(layerCommands.base_layer_change.requiredParams({ name: 'OSM 地图' })).toBe(true);

    const mkMap = (): any => ({
      on: vi.fn(),
      off: vi.fn(),
      once: vi.fn((ev: string, cb: () => void) => {
        if (ev === 'style.load') setTimeout(cb, 1);
      }),
    });
    const hud = { baseLayer: 'Carto 浅色', setBaseLayer: vi.fn() };

    // canonical id should resolve
    const resId = (await layerCommands.base_layer_change.run({
      map: mkMap(),
      params: { id: 'OSM 地图' },
      setSelectedBaseLayer: vi.fn(),
      getHudState: () => hud,
    } as any)) as any;
    expect(resId.status).toBe('succeeded');

    // legacy name still works
    const resName = (await layerCommands.base_layer_change.run({
      map: mkMap(),
      params: { name: 'OSM 地图' },
      setSelectedBaseLayer: vi.fn(),
      getHudState: () => hud,
    } as any)) as any;
    expect(resName.status).toBe('succeeded');
  });

  it('layer_visibility_update accepts canonical id and legacy layer_id', () => {
    expect(layerCommands.layer_visibility_update.requiredParams({ id: 'target-layer-1' })).toBe(true);
    expect(layerCommands.layer_visibility_update.requiredParams({ layer_id: 'target-layer-1' })).toBe(true);

    const mk = (params: any) => {
      const map = makeMockMaplibreMap();
      map.addLayer({ id: 'target-layer-1__point', type: 'circle' });
      map.setLayoutProperty('target-layer-1__point', 'visibility', 'none');
      return { map, params, getHudState: () => ({ layers: [{ id: 'target-layer-1' }], updateLayer: vi.fn() }) };
    };
    const ctxId = mk({ id: 'target-layer-1', visible: false }) as any;
    expect((layerCommands.layer_visibility_update.run(ctxId) as any).status).toBe('succeeded');

    const ctxLegacy = mk({ layer_id: 'target-layer-1', visible: false }) as any;
    // need to reset mock expectation: visibility should be none -> confirmed
    ctxLegacy.map.setLayoutProperty('target-layer-1__point', 'visibility', 'none');
    expect((layerCommands.layer_visibility_update.run(ctxLegacy) as any).status).toBe('succeeded');
  });

  it('layer_style_update accepts canonical id and legacy layer_id', () => {
    expect(layerCommands.layer_style_update.requiredParams({ id: 'target-layer-1', style: { color: '#ff0000' } })).toBe(true);
    expect(layerCommands.layer_style_update.requiredParams({ layer_id: 'target-layer-1', style: { color: '#ff0000' } })).toBe(true);

    const mk = (params: any) => {
      const map = makeMockMaplibreMap();
      map.addLayer({ id: 'target-layer-1__point', type: 'circle' });
      return { map, params, getHudState: () => ({ layers: [{ id: 'target-layer-1', style: {} }], updateLayer: vi.fn() }) };
    };
    expect((layerCommands.layer_style_update.run(mk({ id: 'target-layer-1', style: { color: '#ff0000' } }) as any) as any).status).toBe('succeeded');
    expect((layerCommands.layer_style_update.run(mk({ layer_id: 'target-layer-1', style: { color: '#ff0000' } }) as any) as any).status).toBe('succeeded');
  });

  it('apply_layer_filter accepts canonical id and legacy layer_id', () => {
    expect(layerCommands.apply_layer_filter.requiredParams({ id: 'target-layer-1', filter: ['==', 'x', 1] })).toBe(true);
    expect(layerCommands.apply_layer_filter.requiredParams({ layer_id: 'target-layer-1', filter: ['==', 'x', 1] })).toBe(true);

    const mk = (params: any) => {
      const map = makeMockMaplibreMap();
      map.addLayer({ id: 'target-layer-1__point', type: 'circle' });
      return { map, params, getHudState: () => ({ layers: [{ id: 'target-layer-1' }], updateLayer: vi.fn() }) };
    };
    expect((layerCommands.apply_layer_filter.run(mk({ id: 'target-layer-1', filter: ['==', ['get', 'type'], 'h'] }) as any) as any).status).toBe('succeeded');
    expect((layerCommands.apply_layer_filter.run(mk({ layer_id: 'target-layer-1', filter: ['==', ['get', 'type'], 'h'] }) as any) as any).status).toBe('succeeded');
  });
});
