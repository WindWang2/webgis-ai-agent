import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as navigation from './navigation';
import { layerCommands } from '@/lib/map-commands/layerCommands';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';
import { useHudStore } from '@/lib/store/useHudStore';

describe('focus/bbox must use descriptor.bbox first, no full-FC scan (#668)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useHudStore.setState({ layers: [] });
  });

  it('add_layer flyTo on descriptor-backed MVT layer does not trigger full-FC scan', () => {
    const spyCalc = vi.spyOn(navigation, 'calculateBBox');
    const spyFit = vi.spyOn(navigation, 'fitBounds');

    // Build a large FC that would be expensive to scan
    const bigFC = {
      type: 'FeatureCollection',
      features: Array.from({ length: 50000 }, (_, i) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [i * 0.001, 0] },
        properties: {},
      })),
      bbox: [0, 0, 50, 0] as any,
    };

    // Put a descriptor-backed layer into store (simulates already-mounted MVT layer)
    const layer = {
      id: 'ref:big-1',
      name: 'Big',
      type: 'vector' as const,
      visible: true,
      opacity: 1,
      source: bigFC as any,
      _refId: 'ref:big-1',
      _tileUrl: 'http://localhost:8000/api/v1/layers/data/ref:big-1/tiles/{z}/{x}/{y}.mvt?session_id=sid',
      _descriptor: {
        ref_id: 'ref:big-1',
        feature_count: 50000,
        point_count: 50000,
        geometry_types: ['Point'],
        bbox: [10, 20, 30, 40] as any,
        mvt_capable: true,
        estimated_bytes: 5000000,
        content_hash: null,
      },
    };
    useHudStore.setState({ layers: [layer as any] });

    const map = makeMockMaplibreMap();
    // add_layer is used for custom-* layers; we test that flyTo path prefers descriptor over scanning geojson
    // Simulate a re-flyTo scenario: the geojson payload is large but descriptor exists
    // The command should read descriptor.bbox first, not scan geojson
    const ctx: any = {
      map,
      params: { layerId: 'ref:big-1', geojson: bigFC, flyTo: true, type: 'fill' },
      getHudState: () => useHudStore.getState(),
      setSelectedBaseLayer: () => {},
    };

    layerCommands.add_layer.run(ctx);

    // Must have used descriptor bbox [10,20,30,40], not computed bbox from features
    expect(spyFit).toHaveBeenCalled();
    const fitArg = spyFit.mock.calls[0]?.[1];
    expect(fitArg).toEqual([10, 20, 30, 40]);
    expect(spyCalc).not.toHaveBeenCalled();
  });

  it('calculateBBox is still called as fallback for small layer without descriptor', () => {
    const spyCalc = vi.spyOn(navigation, 'calculateBBox');
    const spyFit = vi.spyOn(navigation, 'fitBounds');
    const smallFC = {
      type: 'FeatureCollection',
      features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [5, 5] }, properties: {} }],
    };
    useHudStore.setState({ layers: [{ id: 'small-1', source: smallFC } as any] });
    const map = makeMockMaplibreMap();
    const ctx: any = {
      map,
      params: { layerId: 'small-1', geojson: smallFC, flyTo: true },
      getHudState: () => useHudStore.getState(),
      setSelectedBaseLayer: () => {},
    };
    layerCommands.add_layer.run(ctx);
    expect(spyCalc).toHaveBeenCalled();
    expect(spyFit).toHaveBeenCalled();
  });
});
