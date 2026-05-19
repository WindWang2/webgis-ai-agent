/**
 * M4 renderer 扩展 helper 单测：
 *   addRasterTileSource / setLayerStackVisibility / addProcessLayerStack /
 *   removeOrphanCustomLayers / enable3DTerrain / disable3DTerrain /
 *   syncLayerZOrder
 *
 * 用纯 mock map（vi.fn）覆盖每个 helper 的关键行为契约，不依赖真实 MapLibre。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  addRasterTileSource,
  setLayerStackVisibility,
  addProcessLayerStack,
  removeOrphanCustomLayers,
  enable3DTerrain,
  disable3DTerrain,
  syncLayerZOrder,
} from './renderer';

function makeMockMap(initial?: { layers?: any[]; sources?: Record<string, any> }) {
  const state = {
    layers: initial?.layers ?? [],
    sources: initial?.sources ?? {},
  } as { layers: any[]; sources: Record<string, any> };
  const layerOps: any[] = [];
  return {
    state,
    layerOps,
    getStyle: vi.fn(() => ({ layers: state.layers, sources: state.sources })),
    getSource: vi.fn((id: string) => state.sources[id]),
    addSource: vi.fn((id: string, def: any) => {
      state.sources[id] = def;
    }),
    removeSource: vi.fn((id: string) => {
      delete state.sources[id];
    }),
    addLayer: vi.fn((layer: any) => {
      state.layers.push(layer);
      layerOps.push({ op: 'add', id: layer.id });
    }),
    removeLayer: vi.fn((id: string) => {
      state.layers = state.layers.filter((l) => l.id !== id);
      layerOps.push({ op: 'remove', id });
    }),
    getLayer: vi.fn((id: string) => state.layers.find((l) => l.id === id)),
    moveLayer: vi.fn((id: string) => layerOps.push({ op: 'move', id })),
    setLayoutProperty: vi.fn(),
    setPaintProperty: vi.fn(),
    setTerrain: vi.fn(),
  };
}

describe('addRasterTileSource', () => {
  it('adds a raster source with tiles[] and default tileSize', () => {
    const m = makeMockMap();
    addRasterTileSource(m as any, 'r1', 'https://tiles.example/{z}/{x}/{y}.png');
    expect(m.addSource).toHaveBeenCalledWith('r1', {
      type: 'raster',
      tiles: ['https://tiles.example/{z}/{x}/{y}.png'],
      tileSize: 256,
    });
  });

  it('accepts an array of URLs unchanged', () => {
    const m = makeMockMap();
    addRasterTileSource(m as any, 'r2', ['a', 'b'], 512);
    expect(m.addSource).toHaveBeenCalledWith('r2', {
      type: 'raster',
      tiles: ['a', 'b'],
      tileSize: 512,
    });
  });

  it('is idempotent — existing source is not re-added', () => {
    const m = makeMockMap({ sources: { r3: { type: 'raster' } } });
    addRasterTileSource(m as any, 'r3', 'x');
    expect(m.addSource).not.toHaveBeenCalled();
  });
});


describe('setLayerStackVisibility', () => {
  it('sets visibility on every layer matching the prefix', () => {
    const m = makeMockMap({
      layers: [
        { id: 'custom-A-fill' },
        { id: 'custom-A-line' },
        { id: 'custom-B-fill' },   // 不匹配 prefix
        { id: 'process-x' },
      ],
    });
    setLayerStackVisibility(m as any, 'custom-A-', false);
    expect(m.setLayoutProperty).toHaveBeenCalledWith('custom-A-fill', 'visibility', 'none');
    expect(m.setLayoutProperty).toHaveBeenCalledWith('custom-A-line', 'visibility', 'none');
    expect(m.setLayoutProperty).not.toHaveBeenCalledWith('custom-B-fill', expect.anything(), expect.anything());
  });

  it('visible=true uses "visible" value', () => {
    const m = makeMockMap({ layers: [{ id: 'p-1' }] });
    setLayerStackVisibility(m as any, 'p-', true);
    expect(m.setLayoutProperty).toHaveBeenCalledWith('p-1', 'visibility', 'visible');
  });

  it('handles empty layers safely', () => {
    const m = makeMockMap();
    expect(() => setLayerStackVisibility(m as any, 'x', true)).not.toThrow();
  });
});


describe('addProcessLayerStack', () => {
  it('adds source + 3 sub-layers when none exist', () => {
    const m = makeMockMap();
    addProcessLayerStack(m as any, 'step-7', { type: 'FeatureCollection', features: [] });
    expect(m.addSource).toHaveBeenCalledWith('process-step-7', expect.objectContaining({ type: 'geojson' }));
    expect(m.layerOps.filter((o) => o.op === 'add').map((o) => o.id)).toEqual([
      'process-step-7-fill',
      'process-step-7-line',
      'process-step-7-point',
    ]);
  });

  it('is idempotent — source already exists short-circuits', () => {
    const m = makeMockMap({ sources: { 'process-s': { type: 'geojson' } } });
    addProcessLayerStack(m as any, 's', {});
    expect(m.addLayer).not.toHaveBeenCalled();
  });
});


describe('removeOrphanCustomLayers', () => {
  it('removes layers + sources whose base id is not in knownIds', () => {
    const m = makeMockMap({
      layers: [
        { id: 'custom-keep-fill' },
        { id: 'custom-keep-line' },
        { id: 'custom-gone-fill' },
        { id: 'other-x' },
      ],
      sources: { 'custom-keep': {}, 'custom-gone': {}, 'other-x': {} },
    });
    removeOrphanCustomLayers(m as any, new Set(['keep']), 'custom-');
    // layer 删除
    const removedIds = m.layerOps.filter((o) => o.op === 'remove').map((o) => o.id);
    expect(removedIds).toContain('custom-gone-fill');
    expect(removedIds).not.toContain('custom-keep-fill');
    expect(removedIds).not.toContain('other-x');
    // source 删除
    expect(m.removeSource).toHaveBeenCalledWith('custom-gone');
    expect(m.removeSource).not.toHaveBeenCalledWith('custom-keep');
  });

  it('uses custom extractBaseId — process- 单段 id 场景', () => {
    const m = makeMockMap({
      layers: [
        { id: 'process-A-fill' },
        { id: 'process-B-line' },
      ],
      sources: { 'process-A': {}, 'process-B': {} },
    });
    removeOrphanCustomLayers(m as any, new Set(['A']), 'process-');
    // 默认 extractBaseId 已正确处理 fill/line 后缀
    const removed = m.layerOps.filter((o) => o.op === 'remove').map((o) => o.id);
    expect(removed).toContain('process-B-line');
    expect(removed).not.toContain('process-A-fill');
  });
});


describe('3D terrain', () => {
  it('enable3DTerrain adds raster-dem source and calls setTerrain', () => {
    const m = makeMockMap();
    enable3DTerrain(m as any, { exaggeration: 2 });
    expect(m.addSource).toHaveBeenCalledWith('terrain-aws', expect.objectContaining({
      type: 'raster-dem',
      tileSize: 256,
    }));
    expect(m.setTerrain).toHaveBeenCalledWith({ source: 'terrain-aws', exaggeration: 2 });
  });

  it('enable3DTerrain is idempotent on source', () => {
    const m = makeMockMap({ sources: { 'terrain-aws': { type: 'raster-dem' } } });
    enable3DTerrain(m as any);
    expect(m.addSource).not.toHaveBeenCalled();
    expect(m.setTerrain).toHaveBeenCalledOnce();
  });

  it('disable3DTerrain calls setTerrain(null)', () => {
    const m = makeMockMap();
    disable3DTerrain(m as any);
    expect(m.setTerrain).toHaveBeenCalledWith(null);
  });
});


describe('syncLayerZOrder', () => {
  it('calls moveLayer for each matching sub-layer in correct order', () => {
    const m = makeMockMap({
      layers: [
        { id: 'custom-A-fill' },
        { id: 'custom-B-fill' },
        { id: 'custom-A-line' },
      ],
    });
    syncLayerZOrder(m as any, 'custom-', ['A', 'B']);
    // ordered ['A','B'] → reversed → 先 B 后 A，所以 A 落在最顶
    const moves = m.layerOps.filter((o) => o.op === 'move').map((o) => o.id);
    // B 先被 move
    expect(moves[0]).toBe('custom-B-fill');
    // A 的多个 sub 都被 move
    expect(moves).toContain('custom-A-fill');
    expect(moves).toContain('custom-A-line');
  });

  it('skips layers not present without throwing', () => {
    const m = makeMockMap({ layers: [{ id: 'custom-X-fill' }] });
    expect(() => syncLayerZOrder(m as any, 'custom-', ['Y'])).not.toThrow();
  });
});
