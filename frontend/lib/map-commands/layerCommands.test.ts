import { beforeEach, describe, expect, it, vi } from 'vitest';
import { layerCommands } from './layerCommands';
import type { MapCommandContext } from './types';
import * as renderer from '@/lib/map-kit/renderer';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';
import { resolveLayerTargetsByRef } from './layerCommands';
import { commitMapSpecDocument, getPendingPresentation, resetLiveState } from '@/lib/mapspec/session-cursor';

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

// ─── #609: layer_visibility_update null 语义 ───
// 后端 set_layer_status 的 Optional 参数未传时可能以 JSON null 到达（旧版
// layer_manager 直接序列化 None）。null 在 JS 里 `!== undefined` 为真、
// falsy 分支会把图层隐藏；且后验证读到 'none' 与"预期"一致 → 假收敛 confirmed。
// 修复后 null 一律视为"该属性未被请求"：跳过 mutation、跳过 store 写入、
// 跳过对该属性的读回比对。命令单测即 issue 要求的回归测试。

function visCtx(map: any, layers: any[] = []) {
  const updateLayer = vi.fn();
  const hud = { layers, updateLayer };
  const ctx = {
    map,
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => hud,
    setSelectedBaseLayer: () => {},
    command: 'layer_visibility_update',
    params: {},
  } as unknown as MapCommandContext;
  return { ctx, updateLayer, hud };
}

describe('layer_visibility_update (#609 null semantics)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('treats visible:null as "不修改该属性" — 只传 opacity 时图层不被隐藏、store 不写 null', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    map.addLayer({ id: 'result__line', type: 'line' });
    const { ctx, updateLayer } = visCtx(map, [{ id: 'result' }]);
    ctx.params = { layer_id: 'result', visible: null, opacity: 0.5 } as any;

    const result = layerCommands.layer_visibility_update.run(ctx);

    // 事务契约：confirmed + target_ids（目标解析证据）
    expect(result).toEqual({
      status: 'succeeded',
      result: { confirmed: true, target_ids: ['result'] },
    });
    // 透明度照常应用；可见性必须是"未请求"（undefined），绝不能是 'none'
    expect(renderer.updateLayerStyle).toHaveBeenCalledWith(
      map,
      'result__fill',
      expect.objectContaining({ opacity: 0.5, visibility: undefined }),
    );
    expect(renderer.updateLayerStyle).toHaveBeenCalledWith(
      map,
      'result__line',
      expect.objectContaining({ opacity: 0.5, visibility: undefined }),
    );
    expect(renderer.updateLayerStyle).not.toHaveBeenCalledWith(
      map,
      expect.anything(),
      expect.objectContaining({ visibility: 'none' }),
    );
    // store 同步只写 opacity，visible:null 不得落进 store
    expect(updateLayer).toHaveBeenCalledWith('result', { opacity: 0.5 });
  });

  it('visible:null + opacity:null 整体视为无请求 — 不改 map、不写 store、ack 收敛', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    const { ctx, updateLayer } = visCtx(map, [{ id: 'result' }]);
    ctx.params = { layer_id: 'result', visible: null, opacity: null } as any;

    const result = layerCommands.layer_visibility_update.run(ctx);

    expect(result).toEqual({
      status: 'succeeded',
      result: { confirmed: true, target_ids: ['result'] },
    });
    // opacity 必须归一为 undefined：renderer 以 `opacity !== undefined` 判断，
    // 原样传 null 会走 setPaintProperty(prop, null) 重置为默认值。
    expect(renderer.updateLayerStyle).toHaveBeenCalledWith(
      map,
      'result__fill',
      expect.objectContaining({ visibility: undefined, opacity: undefined }),
    );
    expect(renderer.updateLayerStyle).not.toHaveBeenCalledWith(
      map,
      expect.anything(),
      expect.objectContaining({ opacity: null }),
    );
    expect(updateLayer).not.toHaveBeenCalled();
  });

  it('explicit visible:false 仍然隐藏图层并落 store（null 只跳过、不改语义）', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    const { ctx, updateLayer } = visCtx(map, [{ id: 'result' }]);
    ctx.params = { layer_id: 'result', visible: false };

    // renderer.updateLayerStyle 在本测试被 mock——模拟真实 renderer 落盘可见性，
    // 让 FIX-B 后验证能读到迁移后的值（否则 honest ack 是 store_updated）。
    map.setLayoutProperty('result__fill', 'visibility', 'none');

    const result = layerCommands.layer_visibility_update.run(ctx);

    expect(result).toEqual({
      status: 'succeeded',
      result: { confirmed: true, target_ids: ['result'] },
    });
    expect(renderer.updateLayerStyle).toHaveBeenCalledWith(
      map,
      'result__fill',
      expect.objectContaining({ visibility: 'none' }),
    );
    expect(updateLayer).toHaveBeenCalledWith('result', { visible: false });
  });
});

// ─── 跨 id 体系目标解析：ref:geojson-* → 恢复会话的 result-*/product-* 层 ───
// 2026-08-26：set_layer_status/display_layer 解析出数据 ref 下发，但恢复后的
// store 层 id 是 MapSpec 层 id —— 命令全部 target_not_found，工具却已报
// success（假成功）。解析链：store id 直接命中 → _refId → committed spec
// 的 ref→source→layer。

describe('layer_visibility_update 跨 id 体系解析', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLiveState();
  });

  function restoredCtx(refId: string) {
    // 模拟恢复后的 store：层 id = MapSpec 层 id，无 _refId（旧持久化形态）
    const layers = [
      { id: 'product-abc-heatmap', visible: true, opacity: 1, group: 'analysis', style: {} },
      { id: 'product-abc-points', visible: true, opacity: 1, group: 'analysis', style: {} },
    ];
    const updateLayer = vi.fn();
    const map = makeMockMaplibreMap();
    const hud = { layers, updateLayer };
    const ctx = {
      map,
      popAction: () => {},
      setDeferredPop: () => {},
      safePop: () => {},
      getHudState: () => hud,
      setSelectedBaseLayer: () => {},
      command: 'layer_visibility_update',
      params: { layer_id: refId, visible: false },
    } as unknown as MapCommandContext;
    return { ctx, updateLayer, layers };
  }

  it('ref 经 committed spec(ref→source→layer)解析到恢复层，同 ref 多层全部生效', () => {
    commitMapSpecDocument({
      version: '1.0',
      sources: {
        'webgis_map_product_layer_source': { type: 'geojson', ref_id: 'ref:geojson-xyz' },
        unrelated: { type: 'geojson', ref_id: 'ref:geojson-other' },
      },
      layers: [
        { id: 'product-abc-heatmap', source: 'webgis_map_product_layer_source', type: 'heatmap' },
        { id: 'product-abc-points', source: 'webgis_map_product_layer_source', type: 'circle' },
      ],
    } as any);
    const { ctx, updateLayer } = restoredCtx('ref:geojson-xyz');

    const result = layerCommands.layer_visibility_update.run(ctx) as any;

    expect(result.status).toBe('succeeded');
    // 两层都被隐藏（store 更新 + #737 pending presentation）
    const ids = updateLayer.mock.calls.map((c: any[]) => c[0]);
    expect(ids).toContain('product-abc-heatmap');
    expect(ids).toContain('product-abc-points');
    expect(getPendingPresentation()['product-abc-heatmap']).toEqual({ visible: false });
    expect(getPendingPresentation()['product-abc-points']).toEqual({ visible: false });
  });

  it('store 层 _refId 命中（在飞会话 HUD 层形态）', () => {
    const layers = [{ id: 'ref:geojson-live', _refId: 'ref:geojson-live', visible: true }];
    const updateLayer = vi.fn();
    const map = makeMockMaplibreMap();
    const ctx = {
      map, popAction: () => {}, setDeferredPop: () => {}, safePop: () => {},
      getHudState: () => ({ layers, updateLayer }),
      setSelectedBaseLayer: () => {},
      command: 'layer_visibility_update',
      params: { layer_id: 'ref:geojson-live', visible: false },
    } as unknown as MapCommandContext;
    expect((layerCommands.layer_visibility_update.run(ctx) as any).status).toBe('succeeded');
    expect(updateLayer).toHaveBeenCalledWith('ref:geojson-live', { visible: false });
  });

  it('完全未知的 ref 仍然 target_not_found', () => {
    commitMapSpecDocument({ version: '1.0', sources: {}, layers: [] } as any);
    const { ctx } = restoredCtx('ref:geojson-nope');
    const result = layerCommands.layer_visibility_update.run(ctx);
    expect(result).toEqual({ status: 'failed', error: 'target_not_found' });
  });

  it('resolveLayerTargetsByRef 三级解析链', () => {
    // 1. 直接命中
    expect(
      resolveLayerTargetsByRef('a1', () => ({ layers: [{ id: 'a1' }] }) as any),
    ).toEqual(['a1']);
    // 2. _refId 命中
    expect(
      resolveLayerTargetsByRef('ref:r', () => ({ layers: [{ id: 'L1', _refId: 'ref:r' }] }) as any),
    ).toEqual(['L1']);
    // 3. committed spec 命中 —— spec-only 层（无 store 行）也是合法目标：
    // product-* 直写层可能尚未镜像成 HUD 行，漏了它 ref 定向隐藏就漏网
    // （2026-08-25 会话回归：POI 点隐藏失败）。
    commitMapSpecDocument({
      version: '1.0',
      sources: { s1: { type: 'geojson', ref_id: 'ref:r2' } },
      layers: [{ id: 'spec-only', source: 's1', type: 'circle' }],
    } as any);
    expect(
      resolveLayerTargetsByRef('ref:r2', () => ({ layers: [] }) as any),
    ).toEqual(['spec-only']);
  });

  it('spec-only 层（无 store 行）的 ref 隐藏经 pending presentation 落到 spec 层', () => {
    commitMapSpecDocument({
      version: '1.0',
      sources: { s1: { type: 'geojson', ref_id: 'ref:poi' } },
      layers: [{ id: 'product-xyz-points', source: 's1', type: 'circle' }],
    } as any);
    const updateLayer = vi.fn();
    const map = makeMockMaplibreMap();
    const ctx = {
      map, popAction: () => {}, setDeferredPop: () => {}, safePop: () => {},
      getHudState: () => ({ layers: [], updateLayer }),
      setSelectedBaseLayer: () => {},
      command: 'layer_visibility_update',
      params: { layer_id: 'ref:poi', visible: false },
    } as unknown as MapCommandContext;

    const result = layerCommands.layer_visibility_update.run(ctx) as any;

    expect(result.status).toBe('succeeded');
    expect(getPendingPresentation()['product-xyz-points']).toEqual({ visible: false });
  });
});

// ─── finalize_display：分析收尾的显示管理钩子 ───

describe('finalize_display（最终展示集收口）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLiveState();
  });

  function ctxWith(showIds: string[]) {
    // 混合场景:恢复层(mapspec id) + HUD 层(ref id) + base 组
    const layers = [
      { id: 'choropleth-1', visible: true, group: 'analysis' },
      { id: 'poi-points', visible: true, group: 'analysis', _refId: 'ref:geojson-poi' },
      { id: 'boundary', visible: true, group: 'analysis' },
      { id: 'basemap-x', visible: true, group: 'base' },
    ];
    const updateLayer = vi.fn();
    const map = makeMockMaplibreMap();
    const ctx = {
      map, popAction: () => {}, setDeferredPop: () => {}, safePop: () => {},
      getHudState: () => ({ layers, updateLayer }),
      setSelectedBaseLayer: () => {},
      command: 'finalize_display',
      params: { show_layer_ids: showIds },
    } as unknown as MapCommandContext;
    return { ctx, updateLayer };
  }

  it('展示列出层、隐藏其余分析层、不动 base 组、pending presentation 同步', () => {
    commitMapSpecDocument({
      version: '1.0',
      sources: { src1: { type: 'geojson', ref_id: 'ref:geojson-choro' } },
      layers: [{ id: 'choropleth-1', source: 'src1', type: 'fill' }],
    } as any);
    const { ctx, updateLayer } = ctxWith(['ref:geojson-choro']);

    const result = layerCommands.finalize_display.run(ctx) as any;

    expect(result.status).toBe('succeeded');
    // 证据契约：shown/hidden 计数保留；终态集合 + 验证状态显式化
    // （本例 map 无子层——store-owned → store_updated，诚实未收敛）
    expect(result.result.shown).toBe(1);
    expect(result.result.hidden).toBe(2);
    expect(result.result.visible_layer_ids).toEqual(['choropleth-1']);
    expect(result.result.hidden_layer_ids.sort()).toEqual(['boundary', 'poi-points']);
    expect(result.result.unresolved_layer_ids).toEqual([]);
    expect(result.result.confirmed).toBe(false);
    expect(result.result.store_updated).toBe(true);
    const showCalls = updateLayer.mock.calls.filter((c: any[]) => c[1]?.visible === true);
    const hideCalls = updateLayer.mock.calls.filter((c: any[]) => c[1]?.visible === false);
    expect(showCalls.map((c: any[]) => c[0])).toEqual(['choropleth-1']);
    expect(hideCalls.map((c: any[]) => c[0]).sort()).toEqual(['boundary', 'poi-points']);
    // base 组不受影响
    expect(updateLayer).not.toHaveBeenCalledWith('basemap-x', expect.anything());
    // committed spec 层的 pending presentation
    expect(getPendingPresentation()['choropleth-1']).toEqual({ visible: true });
    expect(getPendingPresentation()['poi-points']).toEqual({ visible: false });
  });

  it('空列表 / 全部无法解析 → 显式失败', () => {
    const { ctx } = ctxWith(['ref:geojson-nobody']);
    expect(layerCommands.finalize_display.run(ctx)).toEqual({ status: 'failed', error: 'target_not_found' });
    const empty = ctxWith([]);
    expect(layerCommands.finalize_display.run(empty.ctx)).toEqual({ status: 'failed', error: 'invalid_params' });
  });
});

describe('finalize_display 边界层豁免（context_role=boundary 常显）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLiveState();
  });

  it('行政区边界层不被收口隐藏', () => {
    commitMapSpecDocument({
      version: '1.0',
      sources: {
        s: { type: 'geojson', ref_id: 'ref:geojson-final' },
        b: { type: 'geojson', ref_id: 'ref:geojson-boundary' },
      },
      layers: [
        { id: 'final-1', source: 's', type: 'circle' },
        { id: 'boundary-1', source: 'b', type: 'line', context_role: 'boundary' },
      ],
    } as any);
    const layers = [
      { id: 'final-1', visible: true, group: 'analysis' },
      { id: 'boundary-1', visible: true, group: 'analysis' },
      { id: 'poi', visible: true, group: 'analysis' },
    ];
    const updateLayer = vi.fn();
    const ctx = {
      map: makeMockMaplibreMap(),
      popAction: () => {}, setDeferredPop: () => {}, safePop: () => {},
      getHudState: () => ({ layers, updateLayer }),
      setSelectedBaseLayer: () => {},
      command: 'finalize_display',
      params: { show_layer_ids: ['ref:geojson-final'] },
    } as unknown as MapCommandContext;
    // 只精确解析到 final-1（模拟 resolve 命中一层）：boundary 标记层与 poi 都不在展示集
    const result = layerCommands.finalize_display.run(ctx) as any;
    expect(result.status).toBe('succeeded');
    const hidden = updateLayer.mock.calls.filter((c: any[]) => c[1]?.visible === false).map((c: any[]) => c[0]);
    expect(hidden).toEqual(['poi']); // boundary-1 被豁免
  });
});
