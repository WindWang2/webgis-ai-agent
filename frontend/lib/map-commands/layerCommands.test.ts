import { beforeEach, describe, expect, it, vi } from 'vitest';
import { layerCommands } from './layerCommands';
import type { MapCommandContext } from './types';
import * as renderer from '@/lib/map-kit/renderer';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';

// #462: keep the real layer-id registry exports (matchMapLayers reads them);
// only the style-mutating helpers are stubbed for call assertions.
vi.mock('@/lib/map-kit/renderer', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/map-kit/renderer')>()),
  updateLayerStyle: vi.fn(),
  addGeoJSONLayer: vi.fn(),
  removeLayer: vi.fn(),
}));

function context(layer: Record<string, unknown>, beforeVisible = false) {
  const updateLayer = vi.fn();
  const hud = { layers: [{ ...layer, visible: beforeVisible }], updateLayer };
  const ctx = {
    map: { getStyle: () => ({ layers: [{ id: `${layer.id}__point` }] }) },
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => hud,
    setSelectedBaseLayer: () => {},
    command: 'cartographic_runtime_repair',
    actionId: 'ma-carto-1',
    params: {
      mapspec_fingerprint: 'carto-sha256:current',
      observation_sequence: 4,
      repair_patches: [{
        layer_id: layer.id,
        mapspec_layer_id: 'result',
        before: { visible: false },
        desired: { visible: true },
        rules: ['RUNTIME_RESULT_VISIBILITY'],
      }],
    },
  } as unknown as MapCommandContext;
  return { ctx, updateLayer, hud };
}

describe('cartographic runtime repair command', () => {
  beforeEach(() => vi.clearAllMocks());

  it('applies an AUTO_SAFE presentation patch and stamps its action generation', () => {
    const { ctx, updateLayer } = context({
      id: 'runtime-result',
      _mapspecFingerprint: 'carto-sha256:current',
    });

    const result = layerCommands.cartographic_runtime_repair.run(ctx);

    expect(result).toEqual({
      status: 'succeeded',
      result: {
        confirmed: true,
        repair_action_id: 'ma-carto-1',
        observation_sequence: 4,
      },
    });
    expect(renderer.updateLayerStyle).toHaveBeenCalledWith(
      ctx.map,
      'runtime-result__point',
      expect.objectContaining({ visibility: 'visible' }),
    );
    expect(updateLayer).toHaveBeenCalledWith(
      'runtime-result',
      expect.objectContaining({
        visible: true,
        _mapspecFingerprint: 'carto-sha256:current',
        _mapspecRepairActionId: 'ma-carto-1',
      }),
    );
  });

  it('does not ACK a repair when no live MapLibre layer matched', () => {
    const { ctx, updateLayer } = context({
      id: 'runtime-result',
      _mapspecFingerprint: 'carto-sha256:current',
    });
    (ctx.map as any).getStyle = () => ({ layers: [] });

    const result = layerCommands.cartographic_runtime_repair.run(ctx);

    expect(result).toEqual({ status: 'failed', error: 'target_not_found' });
    expect(renderer.updateLayerStyle).not.toHaveBeenCalled();
    expect(updateLayer).not.toHaveBeenCalled();
  });

  it('does not overwrite a newer user change or stale MapSpec generation', () => {
    const { ctx, updateLayer, hud } = context({
      id: 'runtime-result',
      _mapspecFingerprint: 'carto-sha256:current',
    });
    hud.layers[0].visible = true;

    const result = layerCommands.cartographic_runtime_repair.run(ctx);

    expect(result).toEqual({ status: 'failed', error: 'superseded_by_user' });
    expect(renderer.updateLayerStyle).not.toHaveBeenCalled();
    expect(updateLayer).not.toHaveBeenCalled();
  });

  it('rejects a stale repair even when the user restored the old visible value', () => {
    const { ctx, updateLayer } = context({
      id: 'runtime-result',
      _mapspecFingerprint: 'carto-sha256:current',
      _intentGeneration: 8,
    });
    (ctx.params.repair_patches as any[])[0].before._intentGeneration = 7;

    const result = layerCommands.cartographic_runtime_repair.run(ctx);

    expect(result).toEqual({ status: 'failed', error: 'superseded_by_user' });
    expect(renderer.updateLayerStyle).not.toHaveBeenCalled();
    expect(updateLayer).not.toHaveBeenCalled();
  });
});

// ─── Issue #393: legacy imperative map commands must not silently no-op ───

function layerCtx(map: any, layers: any[] = []) {
  const updateLayer = vi.fn();
  const reorderLayers = vi.fn();
  const hud = { layers, updateLayer, reorderLayers };
  const ctx = {
    map,
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => hud,
    setSelectedBaseLayer: () => {},
    command: 'apply_layer_filter',
    params: {},
  } as unknown as MapCommandContext;
  return { ctx, updateLayer, reorderLayers, hud };
}

describe('apply_layer_filter (issue #393: dual-scheme + store sync + verification)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('applies the filter to every `${id}__*` MapSpec sublayer and writes it back to the store', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    map.addLayer({ id: 'result__line', type: 'line' });
    map.addLayer({ id: 'result__point', type: 'circle' });
    const { ctx, updateLayer } = layerCtx(map, [{ id: 'result' }]);
    ctx.params = { layer_id: 'result', filter: ['==', ['get', 'type'], 'mall'] };

    const result = layerCommands.apply_layer_filter.run(ctx);

    expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
    expect(map.setFilter).toHaveBeenCalledWith('result__fill', ['==', ['get', 'type'], 'mall']);
    expect(map.setFilter).toHaveBeenCalledWith('result__line', ['==', ['get', 'type'], 'mall']);
    expect(map.setFilter).toHaveBeenCalledWith('result__point', ['==', ['get', 'type'], 'mall']);
    // store sync: the reconcile re-emits layer.filter, so a bare setFilter is
    // never rolled back by the next reconcile
    expect(updateLayer).toHaveBeenCalledWith('result', { filter: ['==', ['get', 'type'], 'mall'] });
  });

  it('still matches the legacy `custom-` scheme', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'custom-pop', type: 'fill' });
    map.addLayer({ id: 'custom-pop-outline', type: 'line' });
    const { ctx, updateLayer } = layerCtx(map);
    ctx.params = { layer_id: 'pop', filter: ['>', ['get', 'pop'], 1000] };

    const result = layerCommands.apply_layer_filter.run(ctx);

    expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
    expect(map.setFilter).toHaveBeenCalledWith('custom-pop', ['>', ['get', 'pop'], 1000]);
    expect(map.setFilter).toHaveBeenCalledWith('custom-pop-outline', ['>', ['get', 'pop'], 1000]);
    expect(updateLayer).toHaveBeenCalledWith('pop', { filter: ['>', ['get', 'pop'], 1000] });
  });

  it('parses string filters and clears the store filter when the expression is null', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    const { ctx, updateLayer } = layerCtx(map, [{ id: 'result' }]);
    ctx.params = { layer_id: 'result', filter: '["==", "density", 10]' };

    const result = layerCommands.apply_layer_filter.run(ctx);
    expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
    expect(map.setFilter).toHaveBeenCalledWith('result__fill', ['==', 'density', 10]);

    // clearing: filter null → setFilter(null) + store cleared (null)
    ctx.params = { layer_id: 'result', filter: null };
    const cleared = layerCommands.apply_layer_filter.run(ctx);
    expect(cleared).toEqual({ status: 'succeeded', result: { confirmed: true } });
    expect(map.setFilter).toHaveBeenLastCalledWith('result__fill', null);
    expect(updateLayer).toHaveBeenLastCalledWith('result', { filter: null });
  });

  it('fails target_not_found when neither scheme matches and the store has no layer', () => {
    const map = makeMockMaplibreMap();
    const { ctx, updateLayer } = layerCtx(map, []);
    ctx.params = { layer_id: 'ghost', filter: ['==', 'x', 1] };

    const result = layerCommands.apply_layer_filter.run(ctx);

    expect(result).toEqual({ status: 'failed', error: 'target_not_found' });
    expect(map.setFilter).not.toHaveBeenCalled();
    expect(updateLayer).not.toHaveBeenCalled();
  });

  it('does not fake success when the filter never lands on the map (getFilter empty)', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    // setFilter records the call but the layer never reflects it (e.g. a raster
    // layer that rejects filters) — verification must not ack success.
    (map as any).getFilter = vi.fn(() => null);
    const { ctx, updateLayer } = layerCtx(map, [{ id: 'result' }]);
    ctx.params = { layer_id: 'result', filter: ['==', ['get', 'type'], 'mall'] };

    const result = layerCommands.apply_layer_filter.run(ctx);

    // store scheme matched → honest store_updated (non-converging), never confirmed
    expect(result).toEqual({ status: 'succeeded', result: { store_updated: true } });
    expect(updateLayer).not.toHaveBeenCalled();
  });

  it('fails mutation_failed when a custom-scheme layer rejects the filter', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'custom-pop', type: 'raster' });
    (map as any).getFilter = vi.fn(() => null);
    const { ctx } = layerCtx(map);
    ctx.params = { layer_id: 'pop', filter: ['==', 'x', 1] };

    const result = layerCommands.apply_layer_filter.run(ctx);

    expect(result).toEqual({ status: 'failed', error: 'mutation_failed' });
  });
});

describe('reorder_layer (issue #393: MapSpec `${id}__` layers reorder durably)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('reorders a MapSpec layer via the store array (runtime owns map z-order)', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'analysis-result__fill', type: 'fill' });
    map.addLayer({ id: 'analysis-result__point', type: 'circle' });
    map.addLayer({ id: 'other__fill', type: 'fill' });
    const { ctx, reorderLayers } = layerCtx(map, [
      { id: 'other' },
      { id: 'analysis-result' },
    ]);
    ctx.command = 'reorder_layer';
    ctx.params = { layer_id: 'analysis-result', position: 'top' };

    const result = layerCommands.reorder_layer.run(ctx);

    // not the legacy target_not_found — the MapSpec layer IS reorderable
    expect(result).toEqual({ status: 'succeeded', result: { store_updated: true } });
    // index 0 = topmost; the runtime reconcile re-applies map z-order from this
    expect(reorderLayers).toHaveBeenCalledWith([
      { id: 'analysis-result' },
      { id: 'other' },
    ]);
  });

  it('honors bottom / up / down and before(below before_id) semantics', () => {
    const mk = () => layerCtx(
      makeMockMaplibreMap(),
      [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
    );

    let t = mk();
    t.ctx.command = 'reorder_layer';
    t.ctx.params = { layer_id: 'b', position: 'bottom' };
    layerCommands.reorder_layer.run(t.ctx);
    expect(t.reorderLayers).toHaveBeenCalledWith([{ id: 'a' }, { id: 'c' }, { id: 'b' }]);

    t = mk();
    t.ctx.params = { layer_id: 'c', position: 'up' };
    layerCommands.reorder_layer.run(t.ctx);
    expect(t.reorderLayers).toHaveBeenCalledWith([{ id: 'a' }, { id: 'c' }, { id: 'b' }]);

    t = mk();
    t.ctx.params = { layer_id: 'a', position: 'down' };
    layerCommands.reorder_layer.run(t.ctx);
    expect(t.reorderLayers).toHaveBeenCalledWith([{ id: 'b' }, { id: 'a' }, { id: 'c' }]);

    t = mk();
    t.ctx.params = { layer_id: 'a', position: 'before', before_id: 'c' };
    layerCommands.reorder_layer.run(t.ctx);
    expect(t.reorderLayers).toHaveBeenCalledWith([{ id: 'b' }, { id: 'c' }, { id: 'a' }]);
  });

  it('reorders store-only layers (reconcile has not mounted sublayers yet)', () => {
    const map = makeMockMaplibreMap(); // no map layers at all
    const { ctx, reorderLayers } = layerCtx(map, [{ id: 'a' }, { id: 'b' }]);
    ctx.command = 'reorder_layer';
    ctx.params = { layer_id: 'b', position: 'top' };

    const result = layerCommands.reorder_layer.run(ctx);

    expect(result).toEqual({ status: 'succeeded', result: { store_updated: true } });
    expect(reorderLayers).toHaveBeenCalledWith([{ id: 'b' }, { id: 'a' }]);
  });

  it('fails target_not_found for before_id that does not exist', () => {
    const { ctx, reorderLayers } = layerCtx(makeMockMaplibreMap(), [{ id: 'a' }, { id: 'b' }]);
    ctx.command = 'reorder_layer';
    ctx.params = { layer_id: 'b', position: 'before', before_id: 'ghost' };

    const result = layerCommands.reorder_layer.run(ctx);

    expect(result).toEqual({ status: 'failed', error: 'target_not_found' });
    expect(reorderLayers).not.toHaveBeenCalled();
  });

  it('still reorders legacy custom layers on the map (regression)', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'custom-other', type: 'fill' });
    map.addLayer({ id: 'custom-target', type: 'fill' });
    map.addLayer({ id: 'custom-target-outline', type: 'line' });
    const { ctx } = layerCtx(map);
    ctx.command = 'reorder_layer';
    ctx.params = { layer_id: 'target', position: 'top' };

    const result = layerCommands.reorder_layer.run(ctx);

    expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
    expect(map.moveLayer).toHaveBeenCalledWith('custom-target', undefined);
    expect(map.moveLayer).toHaveBeenCalledWith('custom-target-outline', undefined);
  });
});

// ─── #557: layer_style_update contract (params.style flat paint + categorical) ───

function styleCtx(map: any, layers: any[] = []) {
  const updateLayer = vi.fn();
  const hud = { layers, updateLayer };
  const ctx = {
    map,
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => hud,
    setSelectedBaseLayer: () => {},
    command: 'layer_style_update',
    params: {},
  } as unknown as MapCommandContext;
  return { ctx, updateLayer, hud };
}

describe('layer_style_update (#557 contract)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('#557 断点 5: forwards fillOpacity to the renderer and store (0.4 seed reaches paint)', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    const { ctx, updateLayer } = styleCtx(map, [{ id: 'result', style: { color: '#3b82f6' } }]);
    ctx.params = {
      layer_id: 'result',
      style: { color: '#3b82f6', fill: '#3b82f6', fillOpacity: 0.4, strokeColor: '#1d4ed8', strokeWidth: 1.5 },
    };

    const result = layerCommands.layer_style_update.run(ctx);

    expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
    expect(renderer.updateLayerStyle).toHaveBeenCalledWith(
      map,
      'result__fill',
      expect.objectContaining({ fillOpacity: 0.4, color: '#3b82f6' }),
    );
    expect(updateLayer).toHaveBeenCalledWith(
      'result',
      expect.objectContaining({
        style: expect.objectContaining({ fillOpacity: 0.4, strokeWidth: 1.5 }),
      }),
    );
  });

  it('#557 断点 1/3: categorical (field+colorMap, no style) applies a match paint and syncs the store', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    const { ctx, updateLayer } = styleCtx(map, [{ id: 'result', style: {} }]);
    ctx.params = {
      layer_id: 'result',
      field: 'landuse',
      colorMap: { residential: '#fca5a5', commercial: '#93c5fd' },
      baseStyle: { fillOpacity: 0.75, strokeWidth: 0.5 },
    } as any;

    const result = layerCommands.layer_style_update.run(ctx);

    expect(result).toEqual({ status: 'succeeded', result: { confirmed: true } });
    expect(renderer.updateLayerStyle).toHaveBeenCalledWith(
      map,
      'result__fill',
      expect.objectContaining({
        categorical: expect.objectContaining({
          field: 'landuse',
          colorMap: { residential: '#fca5a5', commercial: '#93c5fd' },
          fillOpacity: 0.75,
        }),
      }),
    );
    expect(updateLayer).toHaveBeenCalledWith(
      'result',
      expect.objectContaining({
        style: expect.objectContaining({
          categorical: { field: 'landuse', colorMap: { residential: '#fca5a5', commercial: '#93c5fd' } },
          fillOpacity: 0.75,
        }),
      }),
    );
  });

  it('still rejects a style-less single emission (no field/colorMap) with invalid_params', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    const { ctx } = styleCtx(map, [{ id: 'result' }]);
    ctx.params = { layer_id: 'result' };

    const result = layerCommands.layer_style_update.run(ctx);

    expect(result).toEqual({ status: 'failed', error: 'invalid_params' });
  });

  it('#557 断点 1: legacy top-level style_applied emission (no params.style) is rejected honestly', () => {
    // SSE 工具路径修复前：emitter 把 style_applied 放顶层，useMapBridge 落入 rest
    // → params.style 缺失 → invalid_params。此测试守住"样式必须经 params.style 到达"。
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    const { ctx } = styleCtx(map, [{ id: 'result' }]);
    ctx.params = { layer_id: 'result', style_applied: { color: '#3b82f6' } } as any;

    const result = layerCommands.layer_style_update.run(ctx);

    expect(result).toEqual({ status: 'failed', error: 'invalid_params' });
  });
});
