/* eslint-disable @typescript-eslint/no-require-imports --
 * vi.mock 工厂被 vitest hoist 到顶层 import 之上，引用模块级变量会 TDZ 报错，
 * 只能在工厂内 require（vitest 官方模式）；仅本测试文件适用。 */
/**
 * P2：reconcile 完成回调的代次守卫。
 *
 * mapKey 重建（WebGL 重试）会 dispose 旧 runtime 并为新地图实例新建；
 * 在途的旧 reconcile 落定时，其 .then 收尾会把**旧 spec/is3D** 应用到
 * `mapRef.current`（已是新实例）—— 若晚于新 reconcile，地形与 3D 资产
 * 停留在陈旧状态。本用例用可控 reconcileAsync 精确编排「旧 reconcile
 * 最后落定」，断言陈旧 is3D 不得作用到新地图实例。
 *
 * House style：对齐 map-panel.webgl-retry-sync.test.tsx（每次 MapMock
 * 挂载造一个独立 mock map = 世代实例）。
 */
import { render, act, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { MapPanel } from './map-panel';
import { makeMockMaplibreMap, type MockMaplibreMap } from '../../test/__mocks__/maplibre-map';

const rmg = vi.hoisted(() => ({
  /** 每次 MapMock 挂载 push 一个独立 mock map（按挂载序 = 世代序）。 */
  maps: [] as unknown[],
  lastOnError: null as null | ((e: unknown) => void),
}));

vi.mock('react-map-gl/maplibre', () => {
  const React = require('react');
  type MapMockProps = {
    id?: string;
    children?: ReactNode;
    onLoad?: () => void;
    onError?: (e: unknown) => void;
  };
  const MapMock = React.forwardRef(function MapMock(props: MapMockProps, ref: React.Ref<unknown>) {
    const instance = React.useRef(null);
    if (!instance.current) {
      instance.current = makeMockMaplibreMap();
      rmg.maps.push(instance.current);
    }
    React.useImperativeHandle(ref, () => ({ getMap: () => instance.current }), []);
    if (props.id === 'default') rmg.lastOnError = props.onError ?? null;
    React.useEffect(() => {
      if (props.id === 'default') props.onLoad?.();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return React.createElement('div', { 'data-testid': 'map-mock' }, props.children);
  });
  const PopupMock = (props: { children?: ReactNode }) =>
    React.createElement('div', { 'data-testid': 'popup' }, props.children);
  return { default: MapMock, Popup: PopupMock };
});

const hud = vi.hoisted(() => {
  const initialState = () => ({
    is3D: false,
    processLayers: {} as Record<string, unknown>,
    cartographyTitle: null as string | null,
    viewport: { center: [116.4, 39.9], zoom: 4, bearing: 0, pitch: 0, bounds: undefined },
    focusLayerId: null as string | null,
    aiStatus: 'idle',
    selectedFeature: null as unknown,
    mapLoaded: false,
    layers: [] as unknown[],
    annotations: [] as unknown[],
    activeMapTool: null as string | null,
    baseLayer: 'Carto 深色',
  });
  const actions = {
    setMapLoaded: (v: boolean) => hud.setState({ mapLoaded: v }),
    setIs3D: (v: boolean) => hud.setState({ is3D: v }),
    setSelectedFeature: (f: unknown) => hud.setState({ selectedFeature: f }),
    setViewport: () => undefined,
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

vi.mock('@/lib/store/useHudStore', () => {
  const React = require('react');
  const { useSyncExternalStore } = React;
  const subscribe = (cb: () => void) => {
    hud.listeners.add(cb);
    return () => hud.listeners.delete(cb);
  };
  const useHudStore = (selector: (s: Record<string, unknown>) => unknown) =>
    useSyncExternalStore(subscribe, () => selector(hud.state), () => selector(hud.state));
  const store = useHudStore as unknown as {
    getState: () => Record<string, unknown>;
    setState: (partial: Record<string, unknown>) => void;
  };
  store.getState = () => hud.state;
  store.setState = (partial) => hud.setState(partial);
  return { useHudStore };
});

// 可控 runtime：reconcileAsync 入队挂起，由测试决定落定次序。
const reconcileQueue = vi.hoisted(() => ({
  pending: [] as Array<{ resolve: () => void; is3D: boolean }>,
}));
vi.mock('@/lib/mapspec-runtime', () => ({
  MapSpecRuntime: class {
    constructor(_map: unknown, _opts?: unknown) { /* mock */ }
    reconcileAsync(_spec: unknown): Promise<void> {
      return new Promise<void>((resolve) => {
        reconcileQueue.pending.push({ resolve, is3D: hud.getState().is3D as boolean });
      });
    }
    dispose(): void { /* mock */ }
    setSessionScope(): void { /* mock */ }
    invalidateStyle(): void { /* mock */ }
    getAppliedSpec(): unknown { return null; }
    isPatchInFlight(): boolean { return false; }
  },
}));

vi.mock('@/lib/api/transport', () => ({
  apiFetch: vi.fn(),
  ApiError: class extends Error {},
  describeApiError: (e: unknown, fb: string) => fb,
}));
vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({
    actions: [],
    dispatchAction: vi.fn(),
    popAction: vi.fn(),
    selectedBaseLayer: 1,
    setSelectedBaseLayer: vi.fn(),
    registerSnapshotFn: vi.fn(),
    getMapSnapshot: () => null,
    registerAckSink: () => () => undefined,
  }),
}));
vi.mock('./map-action-handler', () => ({ MapActionHandler: () => null }));
vi.mock('./map-decorations', () => {
  const React = require('react');
  return {
    MapDecorations: React.memo(function MapDecorationsMock() { return React.createElement('div', null); }),
  };
});
vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));
vi.mock('@/lib/i18n/useT', () => ({ useT: () => (key: string) => key }));

const panelProps = {
  layers: [],
  onRemoveLayer: () => undefined,
  onToggleLayer: () => undefined,
  onViewportChange: () => undefined,
};

/** 触发致命 WebGL 错误（handleMapError 的 isFatalWebGLError 分支）。 */
function fireFatalWebglError() {
  act(() => {
    rmg.lastOnError?.({ error: { message: 'WebGL: Context Lost' } });
  });
}

function lastTerrainCall(map: MockMaplibreMap): unknown {
  const calls = map._calls.setTerrain as Array<{ options: unknown }>;
  return calls.length ? calls[calls.length - 1].options : undefined;
}

describe('MapPanel — reconcile 完成回调代次守卫（P2）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hud.reset();
    rmg.maps.length = 0;
    rmg.lastOnError = null;
    reconcileQueue.pending.length = 0;
  });

  it('mapKey 重建后旧 reconcile 最后落定：陈旧 is3D 不得作用到新地图实例', async () => {
    render(<MapPanel {...panelProps} />);
    // 首条 reconcile 在途（is3D=false 世代 = 陈旧代次）。
    await waitFor(() => expect(reconcileQueue.pending.length).toBe(1));
    const stale = reconcileQueue.pending[0];
    expect(stale.is3D).toBe(false);

    // 致命 WebGL 错误 → 重试：mapKey+1，旧 runtime dispose、新地图实例挂载。
    fireFatalWebglError();
    act(() => {
      fireEvent.click(screen.getByText('map.webgl.retry'));
    });
    await waitFor(() => expect(rmg.maps.length).toBe(2));

    // 新一代次切到 3D。
    act(() => { hud.setState({ is3D: true }); });
    await waitFor(() => expect(reconcileQueue.pending.some((p) => p.is3D)).toBe(true));

    const newMap = rmg.maps[1] as MockMaplibreMap;
    // 落定次序：新代次 reconcile 先落定 → 旧 reconcile 最后落定。
    for (const p of reconcileQueue.pending) {
      if (p !== stale) await act(async () => { p.resolve(); });
    }
    await act(async () => { stale.resolve(); });

    // 修复前：旧 reconcile（is3D=false）的收尾最后落定 → disable3DTerrain
    // 作用到新实例，3D 地形被陈旧回滚（setTerrain(null) 是最后一次调用）。
    expect(lastTerrainCall(newMap)).not.toBeNull();
    expect((lastTerrainCall(newMap) as { source?: string } | undefined)?.source).toBe('terrain-aws');
  });
});
