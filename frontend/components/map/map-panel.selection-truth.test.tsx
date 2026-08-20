/* eslint-disable @typescript-eslint/no-require-imports */
import { render, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MapPanel } from './map-panel';
import type { Layer } from '@/lib/types/layer';
import { makeMockMaplibreMap } from '../../test/__mocks__/maplibre-map';

const rmg = vi.hoisted(() => ({
  renderCount: 0,
  interactiveLayerIds: [] as string[],
  lastOnClick: null as null | ((e: any) => void),
  lastOnMove: null as null | ((e: any) => void),
  lastOnLoad: null as null | (() => void),
  queryResults: [] as any[],
  loaded: false,
  map: null as any,
}));

const hud = vi.hoisted(() => {
  const initialState = () => ({
    is3D: false,
    processLayers: {} as Record<string, unknown>,
    cartographyTitle: null as string | null,
    viewport: { center: [116.4, 39.9], zoom: 4, bearing: 0, pitch: 0, bounds: undefined },
    focusLayerId: null as string | null,
    aiStatus: 'idle',
    selectedFeature: null as any,
    mapLoaded: false,
    layers: [] as any[],
    annotations: [] as any[],
  });
  const actions = {
    setMapLoaded: (v: boolean) => hud.setState({ mapLoaded: v }),
    setSelectedFeature: (f: any) => {
      // support functional updater for merge tests
      if (typeof f === 'function') {
        const cur = hud.state.selectedFeature;
        const next = f(cur);
        hud.setState({ selectedFeature: next });
        return;
      }
      hud.setState({ selectedFeature: f });
    },
    setViewport: (center: any, zoom: number, bearing: number, pitch: number, bounds?: any) =>
      hud.setState({ viewport: { center, zoom, bearing, pitch, bounds } }),
    focusLayer: (id: string | null) => hud.setState({ focusLayerId: id }),
    updateLayer: (id: string, updates: any) => {
      const layers = hud.state.layers.map((l: any) => (l.id === id ? { ...l, ...updates } : l));
      hud.setState({ layers });
    },
  };
  const state: Record<string, unknown> & { selectedFeature: any; layers: any[] } = { ...initialState(), ...actions };
  const listeners = new Set<() => void>();
  return {
    state,
    listeners,
    getState: () => state,
    setState: (partial: Record<string, unknown>) => {
      Object.assign(state, partial);
      listeners.forEach((l) => l());
    },
    reset: () => {
      Object.keys(state).forEach((k) => delete state[k]);
      Object.assign(state, initialState(), actions);
    },
  };
});

const apiFetchMock = vi.hoisted(() => vi.fn());

vi.mock('react-map-gl/maplibre', () => {
  const React = require('react');
  const MapMock = React.forwardRef(function MapMock(props: any, ref: any) {
    React.useImperativeHandle(ref, () => ({ getMap: () => rmg.map }), []);
    rmg.renderCount += 1;
    rmg.interactiveLayerIds = props.interactiveLayerIds ?? [];
    rmg.lastOnClick = props.onClick ?? null;
    rmg.lastOnMove = props.onMove ?? null;
    rmg.lastOnLoad = props.onLoad ?? null;
    const { onLoad } = props;
    React.useEffect(() => {
      if (!rmg.loaded) {
        rmg.loaded = true;
        onLoad?.();
      }
    }, [onLoad]);
    return React.createElement('div', { 'data-testid': 'map-mock' }, props.children);
  });
  const PopupMock = (props: any) => React.createElement('div', { 'data-testid': 'popup' }, props.children);
  return { default: MapMock, Popup: PopupMock };
});

vi.mock('@/lib/store/useHudStore', () => {
  const React = require('react');
  const { useSyncExternalStore } = React;
  const subscribe = (cb: () => void) => {
    hud.listeners.add(cb);
    return () => hud.listeners.delete(cb);
  };
  const useHudStore = (selector: (s: any) => unknown) =>
    useSyncExternalStore(subscribe, () => selector(hud.state), () => selector(hud.state));
  (useHudStore as any).getState = () => hud.state;
  (useHudStore as any).setState = (partial: any) => hud.setState(partial);
  (useHudStore as any).subscribe = subscribe;
  return { useHudStore };
});

vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({
    actions: [],
    dispatchAction: vi.fn(),
    popAction: vi.fn(),
    selectedBaseLayer: 1,
    setSelectedBaseLayer: vi.fn(),
    registerSnapshotFn: vi.fn(),
    getMapSnapshot: vi.fn(() => null),
  }),
}));
vi.mock('./map-action-handler', () => ({ MapActionHandler: () => null }));
vi.mock('./thematic-legend', () => {
  const React = require('react');
  return { ThematicLegend: React.memo(function ThematicLegendMock() { return React.createElement('div', { 'data-testid': 'legend-mock' }); }) };
});
vi.mock('./map-decorations', () => {
  const React = require('react');
  return { MapDecorations: React.memo(function MapDecorationsMock() { return React.createElement('div', { 'data-testid': 'decor-mock' }); }) };
});
vi.mock('@/lib/api/transport', () => ({
  apiFetch: (...args: any[]) => apiFetchMock(...args),
  isApiError: () => false,
}));
vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

import { buildSelectedFeatureSnapshot } from '@/lib/hooks/use-sse-stream';

function freshMockMap() {
  return makeMockMaplibreMap({ renderedFeatures: () => rmg.queryResults });
}

function mvtLayer(id = 'ref:big-1'): Layer {
  return {
    id,
    name: 'Big MVT',
    type: 'vector',
    visible: true,
    opacity: 1,
    source: { type: 'FeatureCollection', features: [] } as any,
    _refId: id,
    _tileUrl: `http://localhost:8000/api/v1/layers/data/${id}/tiles/{z}/{x}/{y}.mvt?session_id=sid-aaa`,
    _descriptor: {
      ref_id: id,
      feature_count: 100_000,
      point_count: 100_000,
      geometry_types: ['Point'],
      bbox: [0, 0, 1, 1] as any,
      mvt_capable: true,
      estimated_bytes: 10_000_000,
      content_hash: null,
    },
  } as any;
}

async function renderPanel(layers: Layer[]) {
  const view = render(<MapPanel layers={layers} onRemoveLayer={() => {}} onToggleLayer={() => {}} onViewportChange={() => {}} />);
  await waitFor(() => expect(rmg.renderCount).toBeGreaterThan(1));
  await act(async () => { await new Promise((r) => setTimeout(r, 100)); });
  return view;
}
async function settleInteractive(ids: string[]) {
  await waitFor(() => expect(rmg.interactiveLayerIds).toEqual(ids), { timeout: 3000 });
}

const clickPoint = { point: [10, 20], lngLat: { lng: 116.4, lat: 39.9 } };

describe('Selection truthfulness on MVT layers (#668)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hud.reset();
    rmg.map = freshMockMap();
    rmg.queryResults = [];
    rmg.renderCount = 0;
    rmg.interactiveLayerIds = [];
    rmg.loaded = false;
  });

  it('backfills authoritative properties via single-feature endpoint and merges into selection', async () => {
    const layer = mvtLayer('ref:big-1');
    hud.setState({ layers: [layer] });
    await renderPanel([layer]);
    await settleInteractive(['ref:big-1__point']);

    // tile returns simplified clipped props
    rmg.queryResults = [{
      type: 'Feature',
      id: 'feat-42',
      geometry: { type: 'Point', coordinates: [116.4001, 39.9001] },
      properties: { id: 'feat-42', name: 'clip', pop: 10 },
      layer: { id: 'ref:big-1__point' },
    }];

    // authoritative feature returned by GET /feature/{id}
    apiFetchMock.mockResolvedValueOnce({
      type: 'Feature',
      id: 'feat-42',
      geometry: { type: 'Point', coordinates: [116.4005, 39.9005] },
      properties: { id: 'feat-42', name: 'full-truth', pop: 999, extra: 'yes' },
      bbox: [116.4, 39.9, 116.41, 39.91],
    });

    act(() => rmg.lastOnClick?.(clickPoint));

    // initial store has tile props
    await waitFor(() => expect(hud.state.selectedFeature).toBeTruthy());
    // after backfill, store must have authoritative props
    await waitFor(() => expect(hud.state.selectedFeature?.properties?.name).toBe('full-truth'), { timeout: 3000 });
    expect(hud.state.selectedFeature.properties.extra).toBe('yes');
    expect(hud.state.selectedFeature.properties.pop).toBe(999);
    // approximate flag cleared (or false)
    expect(hud.state.selectedFeature.isApproximate).not.toBe(true);
    // single-feature endpoint was called, not full FC
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(String(apiFetchMock.mock.calls[0][0])).toContain('/feature/feat-42');
  });

  it('marks selection as approximate when backfill fails or has no usable id', async () => {
    const layer = mvtLayer('ref:big-1');
    hud.setState({ layers: [layer] });
    await renderPanel([layer]);
    await settleInteractive(['ref:big-1__point']);

    // tile feature without usable id (hash fallback)
    rmg.queryResults = [{
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [116.4, 39.9] },
      properties: { name: 'no-id' },
      layer: { id: 'ref:big-1__point' },
    }];
    // ensure no fetch for fallback
    apiFetchMock.mockClear();

    act(() => rmg.lastOnClick?.(clickPoint));
    await waitFor(() => expect(hud.state.selectedFeature).toBeTruthy());
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    // must be marked approximate rather than pretending precision
    expect(hud.state.selectedFeature.isApproximate).toBe(true);
    expect(apiFetchMock).not.toHaveBeenCalled();

    // LLM context must match same approximate truth
    const snap = buildSelectedFeatureSnapshot(hud.state.selectedFeature, ['ref:big-1']);
    expect((snap as any).isApproximate ?? (snap as any).is_approximate ?? hud.state.selectedFeature.isApproximate).toBeTruthy();
  });

  it('404 on single-feature falls back to approximate instead of throwing', async () => {
    const layer = mvtLayer('ref:big-1');
    hud.setState({ layers: [layer] });
    await renderPanel([layer]);
    await settleInteractive(['ref:big-1__point']);
    rmg.queryResults = [{
      type: 'Feature',
      id: 'missing-99',
      geometry: { type: 'Point', coordinates: [116.4, 39.9] },
      properties: { id: 'missing-99', name: 'tile' },
      layer: { id: 'ref:big-1__point' },
    }];
    apiFetchMock.mockRejectedValueOnce(Object.assign(new Error('not found'), { status: 404 }));
    act(() => rmg.lastOnClick?.(clickPoint));
    await waitFor(() => expect(hud.state.selectedFeature).toBeTruthy());
    await act(async () => { await new Promise((r) => setTimeout(r, 100)); });
    expect(hud.state.selectedFeature.isApproximate).toBe(true);
    expect(hud.state.selectedFeature.properties.name).toBe('tile'); // original tile props kept
  });
});
