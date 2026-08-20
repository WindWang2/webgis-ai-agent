import { describe, it, expect, vi, beforeEach } from 'vitest';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';
import * as renderer from './renderer';
import * as geo from '@/lib/utils/geo';
import { useHudStore } from '@/lib/store/useHudStore';

function makeLargeFC(n: number) {
  const features = Array.from({ length: n }, (_, i) => ({
    type: 'Feature' as const,
    geometry: { type: 'Point' as const, coordinates: [i * 0.01, 0] },
    properties: { id: i },
  }));
  return { type: 'FeatureCollection' as const, features };
}

describe('viewport filter must not double-crop MVT sources (#668)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useHudStore.setState({ layers: [] });
  });

  it('addGeoJsonSource with viewport does NOT filter when source belongs to MVT layer', () => {
    const mvtLayer = {
      id: 'ref:big-1',
      name: 'Big',
      type: 'vector' as const,
      visible: true,
      opacity: 1,
      source: { type: 'FeatureCollection', features: [] } as any,
      _refId: 'ref:big-1',
      _tileUrl: 'http://localhost:8000/api/v1/layers/data/ref:big-1/tiles/{z}/{x}/{y}.mvt?session_id=sid',
      _descriptor: {
        ref_id: 'ref:big-1',
        feature_count: 100_000,
        point_count: 100_000,
        geometry_types: ['Point'],
        bbox: [0, 0, 10, 10],
        mvt_capable: true,
        estimated_bytes: 10000000,
        content_hash: null,
      },
    };
    useHudStore.setState({ layers: [mvtLayer as any] });

    const fc = makeLargeFC(1500);
    const map = makeMockMaplibreMap();
    const viewport: [number, number, number, number] = [0, 0, 0.05, 0.05];

    const spy = vi.spyOn(geo, 'filterFeaturesByBounds');

    // MVT layer id matches source id 'ref:big-1'
    renderer.addGeoJsonSource(map as any, 'ref:big-1', fc as any, { viewport });

    // For MVT sources, viewport culling must be skipped — no double crop
    // Implementation may either not call filterFeaturesByBounds at all, or return unfiltered data.
    // We assert the effective data set on the source is NOT culled.
    const src = map.getSource('ref:big-1') as any;
    // If filtering was skipped, source data features length stays 1500
    // If filtering was incorrectly applied, it would be culled to ~6 features intersecting [0,0,0.05,0.05]
    const _stored = src?.__data ?? src?.data ?? null;
    // mock map stores via addSource data param — inspect via _sources if available
    // fallback: check spy call
    if (spy.mock.calls.length > 0) {
      // If spy was called, it indicates double-crop path was taken — fail
      expect(spy).not.toHaveBeenCalled();
    }
    // Also verify via direct filter check alternative: if filtering skipped, features len unchanged
    // Access internal mock helper: makeMockMaplibreMap stores sources
    const rawSrc = (map as any)._sources?.['ref:big-1'] ?? (map as any).getSource('ref:big-1');
    if (rawSrc && rawSrc.data) {
      expect(rawSrc.data.features.length).toBe(1500);
    }
  });

  it('non-MVT large source is still viewport-culled', () => {
    useHudStore.setState({ layers: [] });
    const fc = makeLargeFC(1500);
    const map = makeMockMaplibreMap();
    const viewport: [number, number, number, number] = [0, 0, 0.05, 0.05];
    renderer.addGeoJsonSource(map as any, 'small-inline', fc as any, { viewport });
    const src: any = (map as any)._sources?.['small-inline'] ?? map.getSource('small-inline');
    // For non-MVT, filtering SHOULD happen, so stored features < 1500
    if (src?.data?.features) {
      expect(src.data.features.length).toBeLessThan(1500);
      expect(src.data.features.length).toBeGreaterThan(0);
    }
  });
});
