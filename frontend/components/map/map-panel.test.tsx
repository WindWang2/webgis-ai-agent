/* eslint-disable @typescript-eslint/no-require-imports --
 * vi.mock 工厂被 vitest hoist 到顶层 import 之上，引用模块级变量会 TDZ 报错，
 * 只能在工厂内 require（vitest 官方模式）；仅本测试文件适用。 */
import { render, act, waitFor, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MapPanel } from './map-panel';
import type { Layer } from '@/lib/types/layer';
import { makeMockMaplibreMap } from '../../test/__mocks__/maplibre-map';
import { isUserGesturing, _resetCameraArbitrationForTests } from '@/lib/map-commands/camera-arbitration';

/**
 * FE-3 (design §7) MapPanel interaction UX tests.
 *
 * House style: hand-rolled React mocks (react-map-gl wrapper, HUD store) + the
 * shared makeMockMaplibreMap for the underlying MapLibre instance (runtime +
 * renderer run for real). The `rmg` / `hud` / `overlays` hoisted registries
 * bridge the mock factories and the assertions.
 */

const rmg = vi.hoisted(() => ({
  renderCount: 0,
  interactiveLayerIds: [] as string[],
  lastOnMove: null as null | ((e: any) => void),
  lastOnClick: null as null | ((e: any) => void),
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
  });
  const actions = {
    setMapLoaded: (v: boolean) => hud.setState({ mapLoaded: v }),
    setSelectedFeature: (f: any) => hud.setState({ selectedFeature: f }),
    setViewport: (center: any, zoom: number, bearing: number, pitch: number, bounds?: any) =>
      hud.setState({ viewport: { center, zoom, bearing, pitch, bounds } }),
    focusLayer: (id: string | null) => hud.setState({ focusLayerId: id }),
  };
  const state: Record<string, unknown> = { ...initialState(), ...actions };
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

const overlays = vi.hoisted(() => ({ legendRenders: 0, decorRenders: 0 }));

vi.mock('react-map-gl/maplibre', () => {
  const React = require('react');
  const MapMock = React.forwardRef(function MapMock(props: any, ref: any) {
    React.useImperativeHandle(ref, () => ({ getMap: () => rmg.map }), []);
    rmg.renderCount += 1;
    rmg.interactiveLayerIds = props.interactiveLayerIds ?? [];
    rmg.lastOnMove = props.onMove ?? null;
    rmg.lastOnClick = props.onClick ?? null;
    rmg.lastOnLoad = props.onLoad ?? null;
    // Fire style load deterministically inside the mount effect — RTL's render
    // wraps effects in act, so onLoad → setMapReady flushes synchronously
    // (no async setTimeout racing the drain below).
    React.useEffect(() => {
      if (!rmg.loaded) {
        rmg.loaded = true;
        props.onLoad?.();
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return React.createElement('div', { 'data-testid': 'map-mock' }, props.children);
  });
  const PopupMock = (props: any) =>
    React.createElement('div', { 'data-testid': 'popup' }, props.children);
  return { default: MapMock, Popup: PopupMock };
});

vi.mock('@/lib/store/useHudStore', () => {
  const React = require('react');
  const { useSyncExternalStore } = React;
  const subscribe = (cb: () => void) => {
    hud.listeners.add(cb);
    return () => {
      hud.listeners.delete(cb);
    };
  };
  const useHudStore = (selector: (s: any) => unknown) =>
    useSyncExternalStore(subscribe, () => selector(hud.state), () => selector(hud.state));
  useHudStore.getState = () => hud.state;
  useHudStore.setState = (partial: any) => hud.setState(partial);
  useHudStore.subscribe = subscribe;
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
  return {
    ThematicLegend: React.memo(function ThematicLegend() {
      overlays.legendRenders += 1;
      return React.createElement('div', { 'data-testid': 'legend-mock' });
    }),
  };
});

vi.mock('./map-decorations', () => {
  const React = require('react');
  return {
    MapDecorations: React.memo(function MapDecorations() {
      overlays.decorRenders += 1;
      return React.createElement('div', { 'data-testid': 'decor-mock' });
    }),
  };
});

// ─── helpers ────────────────────────────────────────────────────────────────

const noop = () => {};

/** Shared mock map + the extra surface MapPanel/runtime/renderer needs. */
function freshMockMap() {
  const map = makeMockMaplibreMap();
  map.isStyleLoaded = vi.fn(() => true);
  map.setTerrain = vi.fn();
  map.getBounds = vi.fn(() => ({
    getWest: () => 116.3,
    getSouth: () => 39.8,
    getEast: () => 116.5,
    getNorth: () => 40.0,
  }));
  map.queryRenderedFeatures = vi.fn((_point: unknown, _opts: unknown) => rmg.queryResults);
  return map;
}

function pointLayer(id: string, name: string, opts: { legend?: boolean } = {}): Layer {
  return {
    id,
    name,
    type: 'vector',
    visible: true,
    opacity: 1,
    source: {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          properties: { name: `${name}-feat`, v: 1 },
          geometry: { type: 'Point', coordinates: [116.4, 39.9] },
        },
      ],
    },
    style: { color: '#16a34a' },
    ...(opts.legend ? { legend_spec: { type: 'graduated' } as any } : {}),
  };
}

function featureOn(layerId: string, props: Record<string, unknown> = {}, geometry?: any) {
  return {
    type: 'Feature',
    geometry: geometry ?? { type: 'Point', coordinates: [116.4, 39.9] },
    properties: props,
    layer: { id: layerId },
  };
}

const clickPoint = { point: [10, 20], lngLat: { lng: 116.4, lat: 39.9 } };

async function renderPanel(layers: Layer[]) {
  render(
    <MapPanel layers={layers} onRemoveLayer={noop} onToggleLayer={noop} onViewportChange={noop} />,
  );
  // onLoad fires inside the mount effect → mapReady flips within render's act,
  // so the runtime is created + first reconcile enqueued before we proceed.
  await waitFor(() => expect(rmg.renderCount).toBeGreaterThan(1));
  // Drain the runtime's debounced apply chain (setTimeout/rAF macrotask) inside
  // act so the reconcile's completion (appliedSpec → interactive ids) never
  // lands outside an act window.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 100));
  });
}

async function settleInteractive(ids: string[]) {
  await waitFor(() => expect(rmg.interactiveLayerIds).toEqual(ids), { timeout: 3000 });
}

describe('MapPanel — FE-3 interaction UX', () => {
  beforeEach(() => {
    _resetCameraArbitrationForTests();
    hud.reset();
    rmg.map = freshMockMap();
    rmg.queryResults = [];
    rmg.renderCount = 0;
    rmg.interactiveLayerIds = [];
    rmg.loaded = false;
    overlays.legendRenders = 0;
    overlays.decorRenders = 0;
  });

  it('derives interactiveLayerIds from the runtime applied spec', async () => {
    renderPanel([pointLayer('poi', 'POI'), pointLayer('poi_schools', 'Schools')]);
    // Registry path: sublayer ids from appliedSpec — no styledata scan needed.
    await settleInteractive(['poi__point', 'poi_schools__point']);
  });

  it('click stores the PARENT layer id (longest-prefix — poi vs poi_schools)', async () => {
    renderPanel([pointLayer('poi', 'POI'), pointLayer('poi_schools', 'Schools')]);
    await settleInteractive(['poi__point', 'poi_schools__point']);

    rmg.queryResults = [featureOn('poi_schools__point', { name: 'X', a: 1, b: 2, c: 3 })];
    act(() => rmg.lastOnClick?.(clickPoint));

    await waitFor(() => expect(hud.state.selectedFeature).toBeTruthy());
    const sel = hud.state.selectedFeature;
    expect(sel.layerId).toBe('poi_schools'); // parent — NOT the sublayer id
    expect(sel.layerName).toBe('Schools');
    expect(sel.properties).toEqual({ name: 'X', a: 1, b: 2, c: 3 });
    expect(screen.getByText('Schools')).toBeTruthy(); // selection popup open
  });

  it('click on a ref: sublayer restores the refId', async () => {
    renderPanel([pointLayer('ref:geojson-abc', 'Ref Layer')]);
    await settleInteractive(['ref:geojson-abc__point']);

    rmg.queryResults = [featureOn('ref:geojson-abc__point', { name: 'R' })];
    act(() => rmg.lastOnClick?.(clickPoint));

    await waitFor(() => expect(hud.state.selectedFeature).toBeTruthy());
    expect(hud.state.selectedFeature.layerId).toBe('ref:geojson-abc');
    expect(hud.state.selectedFeature.refId).toBe('ref:geojson-abc');
  });

  it('mounts the imperative selection highlight and setData the feature', async () => {
    renderPanel([pointLayer('poi', 'POI')]);
    await settleInteractive(['poi__point']);

    const geometry = { type: 'Point', coordinates: [116.4, 39.9] };
    rmg.queryResults = [featureOn('poi__point', { name: 'X' }, geometry)];
    act(() => rmg.lastOnClick?.(clickPoint));

    await waitFor(() => {
      const hlCalls = rmg.map._calls.setData.filter(
        (c: { id: string }) => c.id === 'claude-selection-highlight',
      );
      expect(hlCalls.length).toBeGreaterThan(0);
      const data = hlCalls[hlCalls.length - 1].data;
      expect(data).toEqual({
        type: 'FeatureCollection',
        features: [
          {
            type: 'Feature',
            geometry,
            properties: { name: 'X' },
          },
        ],
      });
    });
  });

  it('clears selection + highlight when clicking empty space', async () => {
    renderPanel([pointLayer('poi', 'POI')]);
    await settleInteractive(['poi__point']);

    rmg.queryResults = [featureOn('poi__point', { name: 'X' })];
    act(() => rmg.lastOnClick?.(clickPoint));
    await waitFor(() => expect(hud.state.selectedFeature).toBeTruthy());
    await waitFor(() =>
      expect(
        rmg.map._calls.setData.some(
          (c: { id: string }) => c.id === 'claude-selection-highlight',
        ),
      ).toBe(true),
    );

    rmg.queryResults = [];
    act(() => rmg.lastOnClick?.({ point: [50, 60], lngLat: { lng: 117, lat: 40 } }));
    await waitFor(() => expect(hud.state.selectedFeature).toBeNull());
    await waitFor(() => {
      const hlCalls = rmg.map._calls.setData.filter(
        (c: { id: string }) => c.id === 'claude-selection-highlight',
      );
      expect((hlCalls[hlCalls.length - 1].data as any).features).toEqual([]);
    });
  });

  it('shows an overlap picker (top ≤3) when >1 feature and selects the picked one', async () => {
    renderPanel([pointLayer('poi', 'POI'), pointLayer('poi_schools', 'Schools')]);
    await settleInteractive(['poi__point', 'poi_schools__point']);

    rmg.queryResults = [
      featureOn('poi_schools__point', { name: 'S' }),
      featureOn('poi__point', { name: 'P' }),
    ];
    act(() => rmg.lastOnClick?.(clickPoint));

    // Overlap popup lists the candidates for the user to pick.
    expect(await screen.findByText('选择要素')).toBeTruthy();
    expect(screen.getByText('Schools')).toBeTruthy();
    expect(screen.getByText('POI')).toBeTruthy();

    // Pick the poi_schools entry → parent layer id committed + highlight.
    fireEvent.click(screen.getByText('Schools'));
    await waitFor(() => expect(hud.state.selectedFeature?.layerId).toBe('poi_schools'));
    expect(screen.queryByText('选择要素')).toBeNull(); // picker closed
  });

  it('shows an rAF-throttled hover tooltip with layer name + ≤3 props', async () => {
    renderPanel([pointLayer('poi', 'POI')]);
    await settleInteractive(['poi__point']);

    rmg.queryResults = [
      featureOn('poi__point', { a: 1, b: 2, c: 3, d: 4, e: 5 }),
    ];
    act(() => rmg.map._fire('mousemove', clickPoint));

    expect(await screen.findByText('POI')).toBeTruthy();
    expect(screen.getByText('a:')).toBeTruthy();
    expect(screen.getByText('c:')).toBeTruthy();
    expect(screen.queryByText('d:')).toBeNull(); // capped at 3 props
    expect(screen.queryByText('选择要素')).toBeNull();
  });

  it('reports user gestures to camera-arbitration only when originalEvent is present', async () => {
    renderPanel([]);
    await settleInteractive([]);

    const map = rmg.map;
    // All four start events + their ends are wired on the live map.
    for (const evt of ['dragstart', 'zoomstart', 'rotatestart', 'pitchstart']) {
      expect(map.on).toHaveBeenCalledWith(evt, expect.any(Function));
    }
    for (const evt of ['dragend', 'zoomend', 'rotateend', 'pitchend']) {
      expect(map.on).toHaveBeenCalledWith(evt, expect.any(Function));
    }

    expect(isUserGesturing()).toBe(false);
    act(() => map._fire('dragstart', { originalEvent: {} }));
    expect(isUserGesturing()).toBe(true);
    act(() => map._fire('dragend'));
    expect(isUserGesturing()).toBe(false);

    // Programmatic camera moves fire zoomstart WITHOUT originalEvent → ignored.
    act(() => map._fire('zoomstart', {}));
    expect(isUserGesturing()).toBe(false);
  });

  it('move storm: memoized overlays do not re-render per frame', async () => {
    renderPanel([pointLayer('poi', 'POI', { legend: true })]);
    await settleInteractive(['poi__point']);

    expect(overlays.legendRenders).toBe(1);
    expect(overlays.decorRenders).toBe(1);
    overlays.legendRenders = 0;
    overlays.decorRenders = 0;
    const mapRendersBefore = rmg.renderCount;

    // ~60fps synthetic move storm (only the streaming viewState object churns).
    act(() => {
      for (let i = 1; i <= 30; i++) {
        rmg.lastOnMove?.({
          viewState: {
            longitude: 116.4 + i * 0.001,
            latitude: 39.9,
            zoom: 10 + (i % 3) * 0.1,
            bearing: 0,
            pitch: 0,
          },
        });
      }
    });

    // Per-frame churn never happened: memoized heavy subtrees stayed at 0
    // across the whole storm.
    expect(overlays.legendRenders).toBe(0);
    expect(overlays.decorRenders).toBe(0);
    // The Map component itself re-renders per frame (viewState is local state).
    expect(rmg.renderCount).toBeGreaterThan(mapRendersBefore);

    // Settle (100ms viewport-write debounce): the store gets ONE final
    // viewport write — the zoom scale bar legitimately updates once, never per
    // frame. The legend is untouched (its props don't depend on viewport).
    act(() => {
      vi.useFakeTimers();
      vi.advanceTimersByTime(100);
      vi.useRealTimers();
    });
    expect(overlays.legendRenders).toBe(0);
    expect(overlays.decorRenders).toBeLessThanOrEqual(1);
  });
});
