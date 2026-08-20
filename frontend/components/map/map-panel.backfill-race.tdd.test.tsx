/* eslint-disable @typescript-eslint/no-require-imports */
import { render, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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

describe('A2 TDD — Backfill race: superseded fetch must never overwrite newer selection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hud.reset();
    rmg.map = freshMockMap();
    rmg.queryResults = [];
    rmg.renderCount = 0;
    rmg.interactiveLayerIds = [];
    rmg.loaded = false;
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('two rapid selections with same Date.now(): first backfill resolves last must NOT overwrite second', async () => {
    // Force timestamp collision — Date.now returns same ms for both picks
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(1234567890000);
    const layer = mvtLayer('ref:big-1');
    hud.setState({ layers: [layer] });
    await renderPanel([layer]);
    await settleInteractive(['ref:big-1__point']);

    // Deferred promises for each feature fetch
    let resolveA: (v: any) => void;
    let resolveB: (v: any) => void;
    const promiseA = new Promise<any>((res) => { resolveA = res; });
    const promiseB = new Promise<any>((res) => { resolveB = res; });

    apiFetchMock.mockImplementation((url: string) => {
      if (String(url).includes('feat-A')) return promiseA;
      if (String(url).includes('feat-B')) return promiseB;
      return Promise.reject(new Error('unexpected url ' + url));
    });

    // First click: feature A
    rmg.queryResults = [{
      type: 'Feature',
      id: 'feat-A',
      geometry: { type: 'Point', coordinates: [116.4, 39.9] },
      properties: { id: 'feat-A', name: 'clip-A' },
      layer: { id: 'ref:big-1__point' },
    }];
    act(() => rmg.lastOnClick?.(clickPoint));
    await waitFor(() => expect(hud.state.selectedFeature?.featureId).toBe('feat-A'));
    expect(hud.state.selectedFeature.properties.name).toBe('clip-A');

    // Second click immediately (same Date.now): feature B
    rmg.queryResults = [{
      type: 'Feature',
      id: 'feat-B',
      geometry: { type: 'Point', coordinates: [116.41, 39.91] },
      properties: { id: 'feat-B', name: 'clip-B' },
      layer: { id: 'ref:big-1__point' },
    }];
    act(() => rmg.lastOnClick?.(clickPoint));
    await waitFor(() => expect(hud.state.selectedFeature?.featureId).toBe('feat-B'));
    expect(hud.state.selectedFeature.properties.name).toBe('clip-B');

    // Resolve B first (second selection's authoritative), then A (stale)
    await act(async () => {
      resolveB!({
        type: 'Feature',
        id: 'feat-B',
        geometry: { type: 'Point', coordinates: [116.41, 39.91] },
        properties: { id: 'feat-B', name: 'auth-B', pop: 222 },
        bbox: [116.41, 39.91, 116.42, 39.92],
      });
      await new Promise((r) => setTimeout(r, 10));
    });
    // After B resolves, selected should be auth-B
    await waitFor(() => expect(hud.state.selectedFeature.properties.name).toBe('auth-B'), { timeout: 2000 });

    await act(async () => {
      resolveA!({
        type: 'Feature',
        id: 'feat-A',
        geometry: { type: 'Point', coordinates: [116.4, 39.9] },
        properties: { id: 'feat-A', name: 'auth-A-STALE', pop: 111 },
        bbox: [116.4, 39.9, 116.405, 39.905],
      });
      await new Promise((r) => setTimeout(r, 10));
    });

    // Stale A must NOT overwrite B
    expect(hud.state.selectedFeature.featureId).toBe('feat-B');
    expect(hud.state.selectedFeature.properties.name).toBe('auth-B');
    expect(hud.state.selectedFeature.properties.name).not.toBe('auth-A-STALE');

    nowSpy.mockRestore();
  });
});
