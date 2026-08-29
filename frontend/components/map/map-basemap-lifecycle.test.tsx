/* eslint-disable @typescript-eslint/no-require-imports --
 * vi.mock 工厂被 vitest hoist 到顶层 import 之上，引用模块级变量会 TDZ 报错，
 * 只能在工厂内 require（vitest 官方模式）；仅本测试文件适用。 */
import { render, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MapPanel } from './map-panel';
import type { Layer } from '@/lib/types/layer';
import { makeMockMaplibreMap } from '../../test/__mocks__/maplibre-map';
import * as renderer from '@/lib/map-kit/renderer';

/**
 * Combined MapLibre lifecycle scenario for issues #459 / #460 / #461:
 *
 *   reconcile in-flight → basemap setStyle mid-patch → style recovery →
 *   full reapply → actual map state == MapSpec state
 *
 * A basemap switch (map.setStyle) drops every user source+layer — spec
 * sublayers (MapSpecRuntime), the annotation stack, the selection highlight
 * and the imperative custom-* overlays alike — while a debounced reconcile
 * patch may still be queued. The runtime must not let that patch's completion
 * marker resurrect a stale appliedSpec (#459), the recovery reconcile must
 * fully re-apply the spec, and the imperative stacks must come back mounted
 * (#460) and stay above the spec layers once custom overlays are re-added
 * (#461). This single test fails if any of the three regressions returns.
 */

const rmg = vi.hoisted(() => ({
  interactiveLayerIds: [] as string[],
  lastOnClick: null as null | ((e: any) => void),
  lastOnLoad: null as null | (() => void),
  queryResults: [] as any[],
  loaded: false,
  map: null as any,
}));

/** Mutable basemap selection — drives MapPanel's currentMapStyle. */
const basemap = vi.hoisted(() => ({ index: 1 }));

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
    setSelectedFeature: (f: any) => hud.setState({ selectedFeature: f }),
    setViewport: () => {},
    focusLayer: (id: string | null) => hud.setState({ focusLayerId: id }),
  };
  const state: Record<string, unknown> = { ...initialState(), ...actions };
  const listeners = new Set<() => void>();
  return {
    state,
    getState: () => state,
    setState: (partial: Record<string, unknown>) => {
      Object.assign(state, partial);
      listeners.forEach((l) => l());
    },
    reset: () => {
      Object.keys(state).forEach((k) => delete state[k]);
      Object.assign(state, initialState(), actions);
    },
    subscribe: (cb: () => void) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
  };
});

vi.mock('react-map-gl/maplibre', () => {
  const React = require('react');
  const MapMock = React.forwardRef(function MapMock(props: any, ref: any) {
    React.useImperativeHandle(ref, () => ({ getMap: () => rmg.map }), []);
    rmg.interactiveLayerIds = props.interactiveLayerIds ?? [];
    rmg.lastOnClick = props.onClick ?? null;
    rmg.lastOnLoad = props.onLoad ?? null;
    const styleRef = React.useRef(props.mapStyle);
    React.useEffect(() => {
      if (!rmg.loaded) {
        rmg.loaded = true;
        props.onLoad?.();
        return;
      }
      if (styleRef.current !== props.mapStyle) {
        styleRef.current = props.mapStyle;
        // setStyle semantics: every user source+layer is dropped, then the
        // style reloads (styledata / style.load fire after the swap).
        for (const l of [...rmg.map._layers]) rmg.map.removeLayer(l.id);
        for (const id of Object.keys(rmg.map._sources)) rmg.map.removeSource(id);
        rmg.map._fire('styledata');
        rmg.map._fire('load');
      }
    });
    return React.createElement('div', { 'data-testid': 'map-mock' }, props.children);
  });
  const PopupMock = (props: any) =>
    React.createElement('div', { 'data-testid': 'popup' }, props.children);
  return { default: MapMock, Popup: PopupMock };
});

vi.mock('@/lib/store/useHudStore', () => {
  const React = require('react');
  const { useSyncExternalStore } = React;
  const subscribe = (cb: () => void) => hud.subscribe(cb);
  const useHudStore = (selector: (s: any) => unknown) =>
    useSyncExternalStore(
      subscribe,
      () => selector(hud.state),
      () => selector(hud.state),
    );
  useHudStore.getState = () => hud.state;
  useHudStore.setState = (partial: any) => hud.setState(partial);
  return { useHudStore };
});

vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({
    actions: [],
    dispatchAction: vi.fn(),
    popAction: vi.fn(),
    selectedBaseLayer: basemap.index,
    setSelectedBaseLayer: vi.fn(),
    registerSnapshotFn: vi.fn(),
    getMapSnapshot: vi.fn(() => null),
  }),
}));

vi.mock('./map-action-handler', () => ({ MapActionHandler: () => null }));
vi.mock('./thematic-legend', () => ({ ThematicLegend: () => null }));
vi.mock('./map-decorations', () => ({ MapDecorations: () => null }));

// ─── helpers ────────────────────────────────────────────────────────────────

const noop = () => {};

function pointLayer(id: string, name: string): Layer {
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
          properties: { name: `${name}-feat` },
          geometry: { type: 'Point', coordinates: [116.4, 39.9] },
        },
      ],
    },
    style: { color: '#16a34a' },
  };
}

const clickPoint = { point: [10, 20], lngLat: { lng: 116.4, lat: 39.9 } };

async function renderPanel(layers: Layer[]) {
  const view = render(
    <MapPanel layers={layers} onRemoveLayer={noop} onToggleLayer={noop} onViewportChange={noop} />,
  );
  await waitFor(() => expect(rmg.interactiveLayerIds).toEqual(['poi__point', 'schools__point']), {
    timeout: 3000,
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 100));
  });
  return view;
}

function rerenderPanel(view: ReturnType<typeof render>, layers: Layer[]) {
  view.rerender(
    <MapPanel layers={layers} onRemoveLayer={noop} onToggleLayer={noop} onViewportChange={noop} />,
  );
}

async function drainRuntime() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 100));
  });
}

/** Yield ONLY microtasks: lets reconcileAsync's processOne enqueue its patch
 * ops while the debouncer's setTimeout(0) op frame stays pending — the exact
 * in-flight window a basemap switch can land inside. */
async function pumpMicrotasks(ticks = 12) {
  for (let i = 0; i < ticks; i++) await Promise.resolve();
}

describe('MapPanel — basemap switch during in-flight reconcile (#459/#460/#461)', () => {
  beforeEach(() => {
    hud.reset();
    basemap.index = 1;
    rmg.map = makeMockMaplibreMap({ renderedFeatures: () => rmg.queryResults });
    rmg.queryResults = [];
    rmg.interactiveLayerIds = [];
    rmg.loaded = false;
  });

  it('recovers to map state == MapSpec state with all imperative stacks intact', async () => {
    const specLayers = [pointLayer('poi', 'POI'), pointLayer('schools', 'Schools')];
    const view = await renderPanel(specLayers);
    expect(rmg.map.getLayer('poi__point')).toBeTruthy();
    expect(rmg.map.getLayer('schools__point')).toBeTruthy();

    // ── Imperative stacks mounted on the live map ─────────────────────────
    // Annotation (draw_measurement / add_marker output in the store).
    const measurement = {
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [[116.4, 39.9], [116.41, 39.91]] },
      properties: { label: '1.2km' },
    };
    hud.setState({ annotations: [measurement] });
    await drainRuntime(); // post-reconcile raise mounts the annotation stack

    // Selection highlight via a real click pick.
    const geometry = { type: 'Point', coordinates: [116.4, 39.9] };
    rmg.queryResults = [
      {
        type: 'Feature',
        geometry,
        properties: { name: 'X' },
        layer: { id: 'poi__point' },
      },
    ];
    act(() => rmg.lastOnClick?.(clickPoint));
    await waitFor(() => expect(hud.state.selectedFeature).toBeTruthy());
    // v2 重设计：点击只写快照 + 纯 DOM 悬浮窗，不再挂高亮图层。
    expect(rmg.map.getLayer('claude-selection-highlight-fill')).toBeNull();

    // ── Reconcile starts (layer-changing: visibility toggle on poi) ────────
    const changedLayers = specLayers.map((l) =>
      l.id === 'poi' ? { ...l, visible: false } : l,
    );
    rerenderPanel(view, changedLayers);
    await pumpMicrotasks(); // patch ops enqueued; debouncer frame still pending

    // ── Basemap setStyle fires mid-patch ──────────────────────────────────
    basemap.index = 0; // different style object → MapMock simulates setStyle
    rerenderPanel(view, changedLayers);
    // The wipe dropped everything the spec and the commands had mounted.
    expect(rmg.map.getLayer('poi__point')).toBeNull();
    expect(rmg.map.getLayer('claude-annotations-fill')).toBeNull();
    expect(rmg.map.getLayer('claude-selection-highlight-fill')).toBeNull();

    // ── Style recovery + full reapply ─────────────────────────────────────
    await drainRuntime();

    // #459: every spec source/layer is back; interactive ids (derived from
    // appliedSpec once the patch settles) match the spec's sublayers.
    expect(rmg.map.getSource('poi')).toBeTruthy();
    expect(rmg.map.getSource('schools')).toBeTruthy();
    expect(rmg.map.getLayer('poi__point')).toBeTruthy();
    expect(rmg.map.getLayer('schools__point')).toBeTruthy();
    await waitFor(() =>
      expect(rmg.interactiveLayerIds).toEqual(['poi__point', 'schools__point']),
      { timeout: 3000 },
    );
    // appliedSpec == current spec: a subsequent no-change reconcile emits no
    // add/addSource work at all (empty diff against the recorded basis).
    const addsBefore = rmg.map._calls.addLayer.length;
    const srcAddsBefore = rmg.map._calls.addSource.length;
    rerenderPanel(view, [...changedLayers]);
    await drainRuntime();
    expect(rmg.map._calls.addLayer.length).toBe(addsBefore);
    expect(rmg.map._calls.addSource.length).toBe(srcAddsBefore);

    // #460: the annotation stack is re-mounted WITH its data.
    for (const suffix of ['fill', 'line', 'circle', 'label']) {
      expect(rmg.map.getLayer(`claude-annotations-${suffix}`)).toBeTruthy();
    }
    const annotationData = rmg.map._calls.setData
      .filter((c: { id: string }) => c.id === 'claude-annotations')
      .pop();
    expect(annotationData.data.features).toEqual([measurement]);

    // v2 重设计：底图切换后选中态不再重建高亮图层（悬浮窗是纯 DOM，
    // 天然存活于样式重建之外）。
    expect(rmg.map.getLayer('claude-selection-highlight-fill')).toBeNull();
    expect(rmg.map.getLayer('claude-selection-highlight-circle')).toBeNull();
    const order = () => rmg.map._layers.map((l: any) => l.id);

    // ── #461: custom-* overlay re-added after recovery stays above spec ──
    // (the add_layer command path re-runs on the new style; the next
    // layer-changing reconcile must not bury it again). Added through the
    // production helper the command uses, so the layer-id registry sees it.
    renderer.addGeoJsonSource(rmg.map as any, 'custom-poi', {
      type: 'FeatureCollection',
      features: [],
    });
    renderer.addVectorLayer(rmg.map as any, {
      id: 'custom-poi',
      type: 'circle',
      source: 'custom-poi',
      paint: {},
    });
    rerenderPanel(view, [...changedLayers, pointLayer('hospital', 'Hospital')]);
    await drainRuntime();

    const finalOrder = order();
    for (const specLayer of ['poi__point', 'schools__point', 'hospital__point']) {
      expect(finalOrder.indexOf('custom-poi')).toBeGreaterThan(
        finalOrder.indexOf(specLayer),
      );
    }
    // Annotation stack still topmost of the imperative UX band.
    expect(finalOrder.indexOf('claude-annotations-label')).toBeGreaterThan(
      finalOrder.indexOf('custom-poi'),
    );
  });

  it('#1078 FE1 (v2): custom-* overlays survive a basemap switch via the mount registry', async () => {
    const specLayers = [pointLayer('poi', 'POI')];
    const view = render(
      <MapPanel layers={specLayers} onRemoveLayer={noop} onToggleLayer={noop} onViewportChange={noop} />,
    );
    await waitFor(() => expect(rmg.interactiveLayerIds).toEqual(['poi__point']), {
      timeout: 3000,
    });
    await drainRuntime();

    // 命令路径挂载 custom 覆盖层（经生产 renderer 缝 → 进挂载账本）
    renderer.addGeoJsonSource(rmg.map as any, 'custom-v2-poi', {
      type: 'FeatureCollection',
      features: [],
    });
    renderer.addVectorLayer(rmg.map as any, {
      id: 'custom-v2-poi',
      type: 'circle',
      source: 'custom-v2-poi',
      paint: { 'circle-color': '#0ff' },
    });
    expect(rmg.map.getLayer('custom-v2-poi')).toBeTruthy();

    // basemap 切换：setStyle 抹掉一切（含 custom 覆盖层）
    basemap.index = 0;
    rerenderPanel(view, specLayers);
    expect(rmg.map.getLayer('custom-v2-poi')).toBeNull();

    // 恢复 reconcile 完成后：spec 层与 custom 覆盖层都要回来
    await drainRuntime();
    expect(rmg.map.getSource('poi')).toBeTruthy();
    expect(rmg.map.getLayer('custom-v2-poi')).toBeTruthy();
    expect(rmg.map.getSource('custom-v2-poi')).toBeTruthy();
    // 且 custom 带位于 spec 层之上（#461 序保持）
    const order = rmg.map._layers.map((l: any) => l.id);
    expect(order.indexOf('custom-v2-poi')).toBeGreaterThan(order.indexOf('poi__point'));
  });

  it('#605: 3D terrain survives a basemap switch (style-epoch re-mount)', async () => {
    const specLayers = [pointLayer('poi', 'POI')];
    const view = render(
      <MapPanel layers={specLayers} onRemoveLayer={noop} onToggleLayer={noop} onViewportChange={noop} />,
    );
    // renderPanel 辅助函数硬编码双图层 waitFor —— 本测试单图层，手动等收敛
    await waitFor(() => expect(rmg.interactiveLayerIds).toEqual(['poi__point']), {
      timeout: 3000,
    });
    await drainRuntime();

    // 开启 3D → terrain source + setTerrain 落地
    act(() => hud.setState({ is3D: true }));
    await drainRuntime();
    expect(rmg.map.getSource('terrain-aws')).toBeTruthy();
    expect(rmg.map._calls.setTerrain.at(-1)?.options).toMatchObject({
      source: 'terrain-aws',
    });

    // 切换底图：setStyle 冲掉 source + layer（地形一并被丢）
    basemap.index = 0;
    rerenderPanel(view, specLayers);
    // MapMock 同步清空后，恢复 reconcile 仍在跑 —— 此刻 terrain 应已被清空
    expect(rmg.map.getSource('terrain-aws')).toBeNull();

    // style 恢复完成（reconcile .then —— #605 重挂点）后 terrain 重新挂载
    await drainRuntime();
    expect(rmg.map.getSource('terrain-aws')).toBeTruthy();
    const lastTerrain = rmg.map._calls.setTerrain.at(-1)?.options as any;
    expect(lastTerrain).toMatchObject({ source: 'terrain-aws' });

    // 同风格再切一次，terrain 依然保持
    basemap.index = 1;
    rerenderPanel(view, specLayers);
    await drainRuntime();
    expect(rmg.map.getSource('terrain-aws')).toBeTruthy();
    expect(rmg.map._calls.setTerrain.at(-1)?.options).toMatchObject({
      source: 'terrain-aws',
    });
  });

  it('v3(Phase B): native heatmap overlay survives a basemap switch (layer def 在账)', async () => {
    const specLayers = [pointLayer('poi', 'POI')];
    const view = render(
      <MapPanel layers={specLayers} onRemoveLayer={noop} onToggleLayer={noop} onViewportChange={noop} />,
    );
    await waitFor(() => expect(rmg.interactiveLayerIds).toEqual(['poi__point']), {
      timeout: 3000,
    });
    await drainRuntime();

    // add_native_heatmap 命令路径：source 经 addGeoJsonSource 缝、layer 经
    // addNativeHeatmap（v2 不入账 —— basemap 切换后图层永久消失）。
    renderer.addGeoJsonSource(rmg.map as any, 'custom-heat-x', {
      type: 'FeatureCollection',
      features: [],
    });
    renderer.addNativeHeatmap(rmg.map as any, {
      id: 'custom-heat-x', source: 'custom-heat-x',
    });
    expect(rmg.map.getLayer('custom-heat-x')).toBeTruthy();

    basemap.index = 0;
    rerenderPanel(view, specLayers);
    expect(rmg.map.getLayer('custom-heat-x')).toBeNull();

    await drainRuntime();
    expect(rmg.map.getSource('custom-heat-x')).toBeTruthy();
    expect(rmg.map.getLayer('custom-heat-x')).toBeTruthy();
    expect(rmg.map.getLayer('custom-heat-x')?.type).toBe('heatmap');
    // custom 带位于 spec 层之上
    const order = rmg.map._layers.map((l: any) => l.id);
    expect(order.indexOf('custom-heat-x')).toBeGreaterThan(order.indexOf('poi__point'));
  });
});
