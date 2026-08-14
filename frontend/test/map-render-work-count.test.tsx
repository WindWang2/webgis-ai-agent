/* eslint-disable @typescript-eslint/no-require-imports --
 * Vitest hoisted mock factories require internal requires for module-level variables.
 */
import React from 'react';
import { render, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MapPanel } from '@/components/map/map-panel';
import { SpatialCrosshair } from '@/components/map/spatial-crosshair';
import { MapSpecRuntime } from '@/lib/mapspec-runtime/runtime';
import { hudStateToMapSpec } from '@/lib/mapspec-runtime/adapter';
import { calculateBBox } from '@/lib/map-kit/navigation';
import { resolveParentLayerId } from '@/lib/map-kit/interactive-ids';
import type { Layer } from '@/lib/types/layer';
import { makeMockMaplibreMap } from './__mocks__/maplibre-map';
import { _resetCameraArbitrationForTests } from '@/lib/map-commands/camera-arbitration';

/**
 * Deterministic Work-Count Benchmark Harness for Map Frontend Hot-Paths (Scenarios A - J).
 *
 * Measures concrete operations (React renders, reconcile calls, MapLibre mutations,
 * stylesheet scans, store writes, geometry scans) rather than noisy wall-clock timers.
 */

const metrics = vi.hoisted(() => ({
  mapPanelRenders: 0,
  crosshairRenders: 0,
  storeViewportWrites: 0,
  mapLibreGetStyleCalls: 0,
  mapLibreMoveLayerCalls: 0,
  mapLibreGetLayerCalls: 0,
  reconcileAsyncCalls: 0,
  diffSpecsCalls: 0,
}));

const rmg = vi.hoisted(() => ({
  renderCount: 0,
  interactiveLayerIds: [] as string[],
  lastOnMove: null as null | ((e: any) => void),
  lastOnClick: null as null | ((e: any) => void),
  lastOnLoad: null as null | (() => void),
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
    setViewport: (center: any, zoom: number, bearing: number, pitch: number, bounds?: any) => {
      metrics.storeViewportWrites += 1;
      hud.setState({ viewport: { center, zoom, bearing, pitch, bounds } });
    },
    focusLayer: (id: string | null) => hud.setState({ focusLayerId: id }),
    updateLayer: (id: string, updates: any) => {
      hud.setState({
        layers: hud.state.layers.map((l: any) => (l.id === id ? { ...l, ...updates } : l)),
      });
    },
  };
  const state: Record<string, any> = { ...initialState(), ...actions };
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

vi.mock('react-map-gl/maplibre', () => {
  const React = require('react');
  const MapMock = React.forwardRef(function MapMock(props: any, ref: any) {
    React.useImperativeHandle(ref, () => ({ getMap: () => rmg.map }), []);
    rmg.renderCount += 1;
    rmg.interactiveLayerIds = props.interactiveLayerIds ?? [];
    rmg.lastOnMove = props.onMove ?? null;
    rmg.lastOnClick = props.onClick ?? null;
    rmg.lastOnLoad = props.onLoad ?? null;
    const onLoad = props.onLoad;
    React.useEffect(() => {
      if (!rmg.loaded) {
        rmg.loaded = true;
        onLoad?.();
      }
    }, [onLoad]);
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

vi.mock('@/components/map/map-action-handler', () => ({ MapActionHandler: () => null }));
vi.mock('@/components/map/thematic-legend', () => ({
  ThematicLegend: () => React.createElement('div', { 'data-testid': 'legend-mock' }),
}));
vi.mock('@/components/map/map-decorations', () => ({
  MapDecorations: () => React.createElement('div', { 'data-testid': 'decor-mock' }),
}));

function freshInstrumentedMap() {
  const map = makeMockMaplibreMap();
  map.isStyleLoaded = vi.fn(() => true);
  map.setTerrain = vi.fn();
  map.getBounds = vi.fn(() => ({
    getWest: () => 116.3,
    getSouth: () => 39.8,
    getEast: () => 116.5,
    getNorth: () => 40.0,
  }));
  const origGetStyle = map.getStyle;
  map.getStyle = vi.fn(() => {
    metrics.mapLibreGetStyleCalls += 1;
    return origGetStyle();
  });
  const origMoveLayer = map.moveLayer;
  map.moveLayer = vi.fn((id: string, beforeId?: string | null) => {
    metrics.mapLibreMoveLayerCalls += 1;
    return origMoveLayer(id, beforeId);
  });
  const origGetLayer = map.getLayer;
  map.getLayer = vi.fn((id: string) => {
    metrics.mapLibreGetLayerCalls += 1;
    return origGetLayer(id);
  });
  return map;
}

function makeVectorLayer(id: string, name: string, featureCount = 10): Layer {
  const features = [];
  for (let i = 0; i < featureCount; i++) {
    features.push({
      type: 'Feature' as const,
      properties: { id: i, value: i * 10, name: `${name}-${i}` },
      geometry: {
        type: 'Point' as const,
        coordinates: [116.4 + i * 0.01, 39.9 + i * 0.01],
      },
    });
  }
  return {
    id,
    name,
    type: 'vector',
    visible: true,
    opacity: 1,
    source: {
      type: 'FeatureCollection',
      features,
    },
    style: { color: '#16a34a' },
  };
}

describe('Deterministic Work-Count Performance Evidence (Scenarios A - J)', () => {
  beforeEach(() => {
    _resetCameraArbitrationForTests();
    hud.reset();
    rmg.map = freshInstrumentedMap();
    rmg.renderCount = 0;
    rmg.interactiveLayerIds = [];
    rmg.loaded = false;
    metrics.mapPanelRenders = 0;
    metrics.crosshairRenders = 0;
    metrics.storeViewportWrites = 0;
    metrics.mapLibreGetStyleCalls = 0;
    metrics.mapLibreMoveLayerCalls = 0;
    metrics.mapLibreGetLayerCalls = 0;
    metrics.reconcileAsyncCalls = 0;
    metrics.diffSpecsCalls = 0;
  });

  // A. Continuous Pan 200 Times
  it('Scenario A: 200 Continuous Pan Move Events', async () => {
    const layer = makeVectorLayer('layer-1', 'Layer 1');
    const _view = render(
      <div>
        <MapPanel layers={[layer]} onRemoveLayer={() => {}} onToggleLayer={() => {}} />
        <SpatialCrosshair />
      </div>,
    );
    await waitFor(() => expect(rmg.renderCount).toBeGreaterThan(1));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });

    const initialMapRenders = rmg.renderCount;
    const initialStoreWrites = metrics.storeViewportWrites;

    // Simulate 200 consecutive pan movement frames (~3.3 seconds of dragging at 60fps)
    act(() => {
      for (let i = 1; i <= 200; i++) {
        rmg.lastOnMove?.({
          viewState: {
            longitude: 116.4 + i * 0.0005,
            latitude: 39.9 + i * 0.0005,
            zoom: 10,
            bearing: 0,
            pitch: 0,
          },
        });
      }
    });

    const panMoveMapRenders = rmg.renderCount - initialMapRenders;
    const panMoveStoreWrites = metrics.storeViewportWrites - initialStoreWrites;

    // Settle 100ms debounce
    await act(async () => {
      await new Promise((r) => setTimeout(r, 150));
    });

    const settledStoreWrites = metrics.storeViewportWrites - initialStoreWrites;

    console.log('[Scenario A Evidence] 200 Continuous Pans:', {
      mapRendersDuringMovement: panMoveMapRenders,
      storeWritesDuringMovement: panMoveStoreWrites,
      storeWritesAfterSettle: settledStoreWrites,
    });

    // In controlled mode before optimization: panMoveMapRenders == 200
    // After uncontrolled optimization: panMoveMapRenders == 0
    expect(settledStoreWrites).toBe(1); // Store write must coalesce to exactly 1
  });

  // B. Continuous Zoom Gesture (50 zoom increments)
  it('Scenario B: Zoom Gesture (50 zoom frames)', async () => {
    const layer = makeVectorLayer('layer-1', 'Layer 1');
    render(<MapPanel layers={[layer]} onRemoveLayer={() => {}} onToggleLayer={() => {}} />);
    await waitFor(() => expect(rmg.renderCount).toBeGreaterThan(1));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });

    const initialMapRenders = rmg.renderCount;

    act(() => {
      for (let i = 1; i <= 50; i++) {
        rmg.lastOnMove?.({
          viewState: {
            longitude: 116.4,
            latitude: 39.9,
            zoom: 10 + i * 0.1,
            bearing: 0,
            pitch: 0,
          },
        });
      }
    });

    const zoomMapRenders = rmg.renderCount - initialMapRenders;

    await act(async () => {
      await new Promise((r) => setTimeout(r, 150));
    });

    console.log('[Scenario B Evidence] 50 Zoom Steps:', {
      mapRendersDuringZoom: zoomMapRenders,
    });
  });

  // C. 20 Layers Visible Reconciliation & Style Operations
  it('Scenario C: 20 Layers Visible Reconciliation', async () => {
    const layers: Layer[] = [];
    for (let i = 0; i < 20; i++) {
      layers.push(makeVectorLayer(`layer-${i}`, `Layer ${i}`));
    }

    const map = freshInstrumentedMap();
    const rt = new MapSpecRuntime(map);
    const spec = hudStateToMapSpec({
      layers,
      processLayers: {},
      activeFilters: {},
      is3D: false,
    });

    const initialStyleCalls = metrics.mapLibreGetStyleCalls;
    const initialMoveLayerCalls = metrics.mapLibreMoveLayerCalls;

    await rt.reconcileAsync(spec);

    const firstReconcileStyleCalls = metrics.mapLibreGetStyleCalls - initialStyleCalls;
    const firstReconcileMoves = metrics.mapLibreMoveLayerCalls - initialMoveLayerCalls;

    // Second reconcile with IDENTICAL spec
    const styleCallsBeforeIdentical = metrics.mapLibreGetStyleCalls;
    const moveLayerBeforeIdentical = metrics.mapLibreMoveLayerCalls;

    await rt.reconcileAsync(spec);

    const identicalReconcileStyleCalls = metrics.mapLibreGetStyleCalls - styleCallsBeforeIdentical;
    const identicalReconcileMoves = metrics.mapLibreMoveLayerCalls - moveLayerBeforeIdentical;

    console.log('[Scenario C Evidence] 20 Layers Reconcile:', {
      firstReconcileStyleCalls,
      firstReconcileMoves,
      identicalReconcileStyleCalls,
      identicalReconcileMoves,
    });

    rt.dispose();
  });

  // D. 100 Layers Metadata & Parent Resolution
  it('Scenario D: 100 Layers Metadata & Parent Resolution', () => {
    const layerIds: string[] = [];
    for (let i = 0; i < 100; i++) {
      layerIds.push(`layer-${i}`);
    }
    layerIds.push('poi', 'poi_schools');

    // Test parent resolution across 1000 lookup queries
    let resolvedCount = 0;
    const startTime = performance.now();
    for (let k = 0; k < 1000; k++) {
      const parent = resolveParentLayerId('poi_schools__point', layerIds);
      if (parent === 'poi_schools') resolvedCount++;
    }
    const durationMs = performance.now() - startTime;

    console.log('[Scenario D Evidence] 100 Layers 1000 Parent Lookups:', {
      resolvedCount,
      durationMs: durationMs.toFixed(3),
    });
    expect(resolvedCount).toBe(1000);
  });

  // E. Layer Opacity Continuous Drag (20 steps)
  it('Scenario E: Layer Opacity Continuous Drag (20 updates)', async () => {
    const layer = makeVectorLayer('layer-1', 'Layer 1');
    const map = freshInstrumentedMap();
    const rt = new MapSpecRuntime(map);

    let currentLayer = layer;
    let spec = hudStateToMapSpec({ layers: [currentLayer], processLayers: {}, activeFilters: {}, is3D: false });
    await rt.reconcileAsync(spec);

    const initialMoves = metrics.mapLibreMoveLayerCalls;

    // Simulate 20 slider drag ticks
    for (let step = 1; step <= 20; step++) {
      currentLayer = { ...currentLayer, opacity: 0.5 + step * 0.02 };
      spec = hudStateToMapSpec({ layers: [currentLayer], processLayers: {}, activeFilters: {}, is3D: false });
      await rt.reconcileAsync(spec);
    }

    const totalMovesDuringDrag = metrics.mapLibreMoveLayerCalls - initialMoves;
    console.log('[Scenario E Evidence] 20 Opacity Drag Reconciles:', {
      totalMoveLayerCalls: totalMovesDuringDrag,
    });
    rt.dispose();
  });

  // F. Filter Continuous Update
  it('Scenario F: Filter Continuous Update (20 updates)', async () => {
    const layer = makeVectorLayer('layer-1', 'Layer 1');
    const map = freshInstrumentedMap();
    const rt = new MapSpecRuntime(map);

    let spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
    await rt.reconcileAsync(spec);

    const initialMoves = metrics.mapLibreMoveLayerCalls;

    for (let step = 1; step <= 20; step++) {
      const activeFilters = { 'layer-1': [[0, step * 5]] };
      spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters, is3D: false });
      await rt.reconcileAsync(spec);
    }

    const movesDuringFilter = metrics.mapLibreMoveLayerCalls - initialMoves;
    console.log('[Scenario F Evidence] 20 Filter Updates:', {
      movesDuringFilter,
    });
    rt.dispose();
  });

  // G. Focus Layer (Descriptor BBox vs GeoJSON Scan)
  it('Scenario G: Focus Layer (Descriptor BBox vs GeoJSON Coordinate Scan)', () => {
    const layerWithDescriptor: Layer = {
      ...makeVectorLayer('layer-desc', 'Desc Layer', 1000),
      _descriptor: {
        bbox: [110, 30, 120, 40],
        geometry_types: ['Point'],
        feature_count: 1000,
        mvt_capable: true,
      } as any,
    };

    const layerWithoutDescriptor = makeVectorLayer('layer-nodesc', 'No Desc Layer', 1000);

    // Fast path timing
    const t0 = performance.now();
    const bboxDesc = layerWithDescriptor._descriptor?.bbox;
    const descDuration = performance.now() - t0;

    // Scan path timing
    const t1 = performance.now();
    const bboxScan = calculateBBox(layerWithoutDescriptor.source as any);
    const scanDuration = performance.now() - t1;

    console.log('[Scenario G Evidence] Focus Layer BBox Calculation:', {
      descDurationMs: descDuration.toFixed(4),
      scanDurationMs: scanDuration.toFixed(4),
      bboxDesc,
      bboxScan,
    });

    expect(bboxDesc).toEqual([110, 30, 120, 40]);
    expect(bboxScan).toBeTruthy();
  });

  // H. Basemap Switch
  it('Scenario H: Basemap Switch Style Invalidation & Re-Apply', async () => {
    const layer = makeVectorLayer('layer-1', 'Layer 1');
    const map = freshInstrumentedMap();
    const rt = new MapSpecRuntime(map);

    const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
    await rt.reconcileAsync(spec);
    expect(rt.getAppliedSpec()).toBeTruthy();

    // Basemap switch invalidates style
    rt.invalidateStyle();
    expect(rt.getAppliedSpec()).toBeNull();

    // Next reconcile re-applies all layers
    await rt.reconcileAsync(spec);
    expect(rt.getAppliedSpec()).toBeTruthy();

    rt.dispose();
  });

  // I. Session Switch
  it('Scenario I: Session Switch Clean Reconcile & State Isolation', async () => {
    const layer = makeVectorLayer('layer-1', 'Layer 1');
    const map = freshInstrumentedMap();
    const rt = new MapSpecRuntime(map);

    const spec = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
    await rt.reconcileAsync(spec);

    // Dispose old runtime
    rt.dispose();
    expect(rt.getAppliedSpec()).toBeNull();

    // Fresh session runtime
    const map2 = freshInstrumentedMap();
    const rt2 = new MapSpecRuntime(map2);
    await rt2.reconcileAsync(spec);
    expect(rt2.getAppliedSpec()).toBeTruthy();
    rt2.dispose();
  });

  // J. Cartographic Repair Reconcile (Idempotency & Generation Safety)
  it('Scenario J: Cartographic Repair Reconcile Idempotency', async () => {
    const layer = makeVectorLayer('layer-1', 'Layer 1');
    const map = freshInstrumentedMap();
    const rt = new MapSpecRuntime(map);

    const spec1 = hudStateToMapSpec({ layers: [layer], processLayers: {}, activeFilters: {}, is3D: false });
    await rt.reconcileAsync(spec1);

    // Repair action adjusts style
    const repairedLayer = { ...layer, style: { color: '#2563eb' } };
    const spec2 = hudStateToMapSpec({ layers: [repairedLayer], processLayers: {}, activeFilters: {}, is3D: false });
    await rt.reconcileAsync(spec2);

    // Duplicate repair action dispatched with same style
    const initialMoves = metrics.mapLibreMoveLayerCalls;
    await rt.reconcileAsync(spec2);
    const duplicateMoves = metrics.mapLibreMoveLayerCalls - initialMoves;

    console.log('[Scenario J Evidence] Duplicate Repair Reconcile Moves:', {
      duplicateMoves,
    });
    rt.dispose();
  });
});
