/* eslint-disable @typescript-eslint/no-require-imports --
 * vi.mock 工厂被 vitest hoist 到顶层 import 之上，引用模块级变量会 TDZ 报错，
 * 只能在工厂内 require（vitest 官方模式）；仅本测试文件适用。 */
/**
 * #1627：WebGL 重试后主图实例重建（mapKey+1）而 useHudStore.mapLoaded 不复位
 * → ComparisonView（<Map> 的兄弟节点，不随 key 重挂）的主→副相机同步 effect
 * deps 全部不变，`primary.on('move')` 静默停留在已销毁的旧实例上。
 *
 * House style（对齐 map-panel.test.tsx / map-panel.interaction-ir.test.tsx）：
 * 手写 react-map-gl mock + HUD store mock；ComparisonView 与同步 effect 真跑
 * （被测逻辑不 mock 掉）。每次 MapMock 挂载造一个独立 mock map —— 主图重建
 * 即新世代实例，可对旧/新实例分别断言 on/off。
 */
import { render, act, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { MapPanel } from './map-panel';
import { makeMockMaplibreMap, type MockMaplibreMap } from '../../test/__mocks__/maplibre-map';

const rmg = vi.hoisted(() => ({
  renderCount: 0,
  /** 每次 MapMock 挂载 push 一个独立 mock map（按挂载序 = 世代序）。 */
  maps: [] as unknown[],
  lastOnError: null as null | ((e: unknown) => void),
  // maplibre-map mock 是 .ts，vi.mock 工厂内的 require 解析不到 —— 由测试
  // 模块体在首个 render 前注入（工厂只在组件渲染时调用，时序安全）。
  createMap: null as unknown as () => unknown,
}));
rmg.createMap = () => makeMockMaplibreMap();

vi.mock('react-map-gl/maplibre', () => {
  const React = require('react');
  type MapMockProps = {
    id?: string;
    children?: ReactNode;
    onClick?: (e: unknown) => void;
    onMove?: (e: unknown) => void;
    onLoad?: () => void;
    onError?: (e: unknown) => void;
  };
  const MapMock = React.forwardRef(function MapMock(props: MapMockProps, ref: React.Ref<unknown>) {
    // 每实例一个 mock map：主图 key 重建 = 新世代 MapLibre 实例（旧实例的
    // 事件监听随销毁消亡 —— 与真实 react-map-gl 语义一致）。
    const instance = React.useRef(null);
    if (!instance.current) {
      instance.current = rmg.createMap();
      rmg.maps.push(instance.current);
    }
    React.useImperativeHandle(ref, () => ({ getMap: () => instance.current }), []);
    rmg.renderCount += 1;
    // 只捕主图（id="default"）的 onError —— 副图无该 prop，后渲染会覆盖成 null。
    if (props.id === 'default') rmg.lastOnError = props.onError ?? null;
    React.useEffect(() => {
      // 仅主图（map-panel 的 id="default"）自动触发 onLoad —— 本用例只驱动
      // 主图就绪链路；副图就绪（secondaryReady）不参与同步监听挂载门。
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
    comparison: {
      active: false,
      kind: 'swipe',
      primaryLayerId: null as string | null,
      secondaryLayerId: null as string | null,
      syncPan: true,
      syncZoom: true,
      position: 0.5,
    },
  });
  const actions = {
    // spy：断言重试路径确实把 store 门复位（旧实现只翻本地 mapReady）。
    setMapLoaded: vi.fn((v: boolean) => hud.setState({ mapLoaded: v })),
    setSelectedFeature: (f: unknown) => hud.setState({ selectedFeature: f }),
    setViewport: (center: unknown, zoom: number, bearing: number, pitch: number, bounds?: unknown) =>
      hud.setState({ viewport: { center, zoom, bearing, pitch, bounds } }),
    focusLayer: (id: string | null) => hud.setState({ focusLayerId: id }),
    updateComparison: (patch: Record<string, unknown>) =>
      hud.setState({ comparison: { ...(hud.state.comparison as Record<string, unknown>), ...patch } }),
    exitComparison: () =>
      hud.setState({ comparison: { ...(hud.state.comparison as Record<string, unknown>), active: false } }),
    setActiveMapTool: (t: string | null) => hud.setState({ activeMapTool: t }),
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
    subscribe: (cb: () => void) => () => void;
  };
  store.getState = () => hud.state;
  store.setState = (partial) => hud.setState(partial);
  store.subscribe = subscribe;
  return { useHudStore };
});

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
    getMapSnapshot: vi.fn(() => null),
    registerAckSink: () => () => undefined,
  }),
}));
vi.mock('./map-action-handler', () => ({ MapActionHandler: () => null }));
vi.mock('./map-decorations', () => {
  const React = require('react');
  return { MapDecorations: React.memo(function MapDecorationsMock() { return React.createElement('div', null); }) };
});
vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));
vi.mock('@/lib/i18n/useT', () => ({
  useT: () => (key: string) => key,
}));

const panelProps = {
  layers: [],
  onRemoveLayer: () => undefined,
  onToggleLayer: () => undefined,
  onViewportChange: () => undefined,
};

/** 对比模式激活（主/副图层族 A/B，swipe，双向同步全开）。 */
function enterComparison() {
  hud.setState({
    comparison: {
      active: true,
      kind: 'swipe',
      primaryLayerId: 'A',
      secondaryLayerId: 'B',
      syncPan: true,
      syncZoom: true,
      position: 0.5,
    },
  });
}

/** 触发致命 WebGL 错误（handleMapError 的 isFatalWebGLError 分支）。 */
function fireFatalWebglError() {
  act(() => {
    rmg.lastOnError?.({ error: { message: 'WebGL: Context Lost' } });
  });
}

describe('MapPanel — WebGL 重试后主图重建 × ComparisonView 同步（#1627）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hud.reset();
    rmg.maps.length = 0;
    rmg.renderCount = 0;
    rmg.lastOnError = null;
  });

  it('重试 → setMapLoaded(false) 且主→副同步监听在新主图实例上重新武装', async () => {
    enterComparison();
    render(<MapPanel {...panelProps} />);

    // 初始：主图（maps[0]）+ 副图（maps[1]）挂载；主图 onLoad 已翻 store 门。
    await waitFor(() => expect(hud.state.mapLoaded).toBe(true));
    expect(rmg.maps.length).toBe(2);
    const primaryBefore = rmg.maps[0] as MockMaplibreMap;
    const secondary = rmg.maps[1] as MockMaplibreMap;
    expect(primaryBefore.on).toHaveBeenCalledWith('move', expect.any(Function));

    // 基线行为：主图相机移动 → 副图 jumpTo（同步在工作）。
    primaryBefore._setViewport({ center: [121.47, 31.23] });
    secondary.jumpTo.mockClear();
    act(() => {
      primaryBefore._fire('move');
    });
    expect(secondary.jumpTo).toHaveBeenCalledWith(
      expect.objectContaining({ center: [121.47, 31.23] }),
    );

    // 致命 WebGL 错误 → fallback 模态（主图卸载；ComparisonView 保持挂载）。
    fireFatalWebglError();
    expect(screen.getByText('map.webgl.retry')).toBeInTheDocument();

    // 点「重试」：除本地 mapReady/mapKey 外，store 门必须复位（#1627 修复点）。
    act(() => {
      fireEvent.click(screen.getByText('map.webgl.retry'));
    });
    expect(hud.getState().setMapLoaded).toHaveBeenCalledWith(false);

    // 新主图实例（maps[2]）挂载并 onLoad → store 门回 true。
    await waitFor(() => expect(rmg.maps.length).toBe(3));
    await waitFor(() => expect(hud.state.mapLoaded).toBe(true));
    const primaryAfter = rmg.maps[2] as MockMaplibreMap;
    expect(primaryAfter).not.toBe(primaryBefore);

    // 旧实例 off('move') 已摘除；新实例 on('move') 重新武装。
    expect(primaryBefore.off).toHaveBeenCalledWith('move', expect.any(Function));
    expect(primaryAfter.on).toHaveBeenCalledWith('move', expect.any(Function));

    // 行为对账：新主图 move 驱动副图同步；旧实例监听已摘除、不再驱动。
    secondary.jumpTo.mockClear();
    act(() => {
      primaryAfter._fire('move');
    });
    expect(secondary.jumpTo).toHaveBeenCalledTimes(1);
    act(() => {
      primaryBefore._fire('move');
    });
    expect(secondary.jumpTo).toHaveBeenCalledTimes(1);
  });
});
