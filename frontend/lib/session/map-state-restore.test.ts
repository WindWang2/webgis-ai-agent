import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  buildLayerFromRestored,
  restoreSessionMapLayers,
  selectLayersToRestore,
} from './map-state-restore';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));
vi.mock('@/lib/utils/logger', () => ({
  devOnly: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
  safeError: vi.fn(),
}));

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset();
  useHudStore.getState().clearLayers();
  useHudStore.setState({ baseLayer: 'Carto 深色' });
});

describe('selectLayersToRestore', () => {
  it('prefers the current-generation cartographic observation', () => {
    const state = {
      _current_cartographic_fingerprint: 'carto-sha256:new',
      _cartographic_observation: {
        mapspec_fingerprint: 'carto-sha256:new',
        layers: [{ id: 'obs' }],
      },
      layers: [{ id: 'legacy' }],
    };
    expect(selectLayersToRestore(state as any).map((l: any) => l.id)).toEqual(['obs']);
  });

  it('falls back to persisted layers when the observation is stale or absent', () => {
    const stale = {
      _current_cartographic_fingerprint: 'carto-sha256:new',
      _cartographic_observation: {
        mapspec_fingerprint: 'carto-sha256:old',
        layers: [{ id: 'obs' }],
      },
      layers: [{ id: 'legacy' }],
    };
    expect(selectLayersToRestore(stale as any).map((l: any) => l.id)).toEqual(['legacy']);
    expect(selectLayersToRestore({ layers: [{ id: 'legacy' }] } as any).map((l: any) => l.id)).toEqual(['legacy']);
  });
});

describe('buildLayerFromRestored', () => {
  it('maps runtime_store_id, ref tile url and mapspec metadata', () => {
    const layer = buildLayerFromRestored(
      {
        id: 'mapspec-layer',
        _refId: 'ref:geojson-1',
        runtime_store_id: 'ref:geojson-1',
        name: '分析结果',
        type: 'vector',
        visible: true,
        opacity: 0.8,
        projection_fingerprint: 'proj-fp',
        intent_generation: 3,
      },
      'sid-1',
      'carto-fp',
    );
    expect(layer.id).toBe('ref:geojson-1');
    expect(layer.name).toBe('分析结果');
    expect(layer.type).toBe('vector');
    expect(layer.opacity).toBe(0.8);
    expect(layer._tileUrl).toBe(
      'http://localhost:8000/api/v1/layers/data/ref:geojson-1/tiles/{z}/{x}/{y}.mvt?session_id=sid-1'
    );
    expect(layer._mapspecFingerprint).toBe('carto-fp');
    expect(layer._mapspecLayerId).toBe('mapspec-layer');
    expect(layer._mapspecProjectionFingerprint).toBe('proj-fp');
    expect(layer._intentGeneration).toBe(3);
  });

  it('treats raster_image + raster_bbox as a heatmap with the raster source', () => {
    const layer = buildLayerFromRestored(
      {
        id: 'raster-1',
        raster_image: 'data:image/png;base64,xxx',
        raster_bbox: [100, 20, 101, 21],
      },
      'sid-1',
    );
    expect(layer.type).toBe('heatmap');
    expect(layer.source).toEqual({ image: 'data:image/png;base64,xxx', bbox: [100, 20, 101, 21] });
  });
});

describe('restoreSessionMapLayers', () => {
  it('adds restored layers to the store and fetches ref data for non-MVT layers', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: () => Promise.resolve(JSON.stringify({ type: 'FeatureCollection', features: [] })),
    });

    await restoreSessionMapLayers(
      {
        layers: [
          {
            id: 'L1',
            name: 'A',
            type: 'vector',
            visible: true,
            opacity: 1,
            _refId: 'ref:abc',
            source: { type: 'FeatureCollection', features: [] },
          },
        ],
      },
      { sessionId: 'sid-1' }
    );

    expect(useHudStore.getState().layers).toHaveLength(1);
    expect(useHudStore.getState().layers[0]._refId).toBe('ref:abc');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/layers/data/ref%3Aabc?session_id=sid-1'),
      expect.anything()
    );
  });

  it('skips the ref data fetch for MVT-capable large layers (tiles serve them)', async () => {
    await restoreSessionMapLayers(
      {
        layers: [
          {
            id: 'L1',
            name: 'A',
            type: 'vector',
            visible: true,
            opacity: 1,
            _refId: 'ref:big',
            _descriptor: { mvt_capable: true, feature_count: 9000 },
            source: { type: 'FeatureCollection', features: [] },
          },
        ],
      },
      { sessionId: 'sid-1' }
    );

    expect(useHudStore.getState().layers).toHaveLength(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
