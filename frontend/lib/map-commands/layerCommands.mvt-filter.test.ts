import { describe, it, expect, vi, beforeEach } from 'vitest';
import { layerCommands } from './layerCommands';
import type { MapCommandContext } from './types';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';

function mvtLayerCtx(map: any) {
  const updateLayer = vi.fn();
  const layer = {
    id: 'big-mvt',
    _refId: 'ref:big-1',
    _tileUrl: 'http://localhost:8000/api/v1/layers/data/ref:big-1/tiles/{z}/{x}/{y}.mvt?session_id=sid',
    _descriptor: {
      ref_id: 'ref:big-1',
      feature_count: 100_000,
      point_count: 100_000,
      geometry_types: ['Point'],
      bbox: [0, 0, 1, 1],
      mvt_capable: true,
      estimated_bytes: 10000000,
      content_hash: null,
    },
  };
  const hud = { layers: [layer], updateLayer };
  const ctx = {
    map,
    getHudState: () => hud,
    params: { layer_id: 'big-mvt', filter: ['==', ['get', 'type'], 'mall'] },
  } as unknown as MapCommandContext;
  return { ctx, updateLayer, hud };
}

describe('apply_layer_filter honest ack on MVT layer (#667 G risk 4)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('MVT-mounted layer with _tileUrl+_descriptor returns store_updated, not confirmed', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'big-mvt__point', type: 'circle' });
    // getFilter returns the filter (would normally pass validation)
    (map as any).getFilter = vi.fn(() => ['==', ['get', 'type'], 'mall']);
    const { ctx, updateLayer } = mvtLayerCtx(map);

    const result = layerCommands.apply_layer_filter.run(ctx);

    expect(result).toEqual({ status: 'succeeded', result: { store_updated: true } });
    // filter still stored in HUD
    expect(updateLayer).toHaveBeenCalledWith('big-mvt', { filter: ['==', ['get', 'type'], 'mall'] });
    expect((result as any).result.confirmed).toBeUndefined();
  });
});
