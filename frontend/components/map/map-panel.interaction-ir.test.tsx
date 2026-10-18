/* eslint-disable @typescript-eslint/no-require-imports */
/**
 * Interaction IR review 清偿（P1-4 机器验证）：程序性相机移动（agent
 * flyTo/set_view，无 originalEvent）不得产生用户 viewport 交互事件 ——
 * FE-3 同款手势判别在 ADR-0227 viewport 事件源上的应用。
 */
import { render, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { MapPanel } from './map-panel';
import { makeMockMaplibreMap } from '../../test/__mocks__/maplibre-map';
import { emitViewportSettled } from '@/lib/interaction/bus';
import { resetViewportSeq, viewportSeqTracker } from '@/lib/utils/viewport-seq';

vi.mock('@/lib/interaction/bus', () => ({
  emitViewportSettled: vi.fn(),
  emitFeatureDeselected: vi.fn(),
  emitMeasurementCreated: vi.fn(),
}));

const rmg = vi.hoisted(() => ({
  renderCount: 0,
  lastOnClick: null as null | ((e: unknown) => void),
  lastOnMove: null as null | ((e: unknown) => void),
  lastOnLoad: null as null | (() => void),
  loaded: false,
  map: null as unknown,
}));

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
    layers: [] as Array<{ id: string } & Record<string, unknown>>,
    annotations: [] as unknown[],
    activeMapTool: null as string | null,
  });
  const actions = {
    setMapLoaded: (v: boolean) => hud.setState({ mapLoaded: v }),
    setSelectedFeature: (f: unknown) => hud.setState({ selectedFeature: f }),
    setViewport: (center: unknown, zoom: number, bearing: number, pitch: number, bounds?: unknown) =>
      hud.setState({ viewport: { center, zoom, bearing, pitch, bounds } }),
    focusLayer: (id: string | null) => hud.setState({ focusLayerId: id }),
    updateLayer: (id: string, updates: Record<string, unknown>) => {
      const current = hud.state.layers as Array<{ id: string } & Record<string, unknown>>;
      const layers = current.map((l) => (l.id === id ? { ...l, ...updates } : l));
      hud.setState({ layers });
    },
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

const apiFetchMock = vi.hoisted(() => vi.fn());

vi.mock('react-map-gl/maplibre', () => {
  const React = require('react');
  type MapMockProps = {
    children?: ReactNode;
    onClick?: (e: unknown) => void;
    onMove?: (e: unknown) => void;
    onLoad?: () => void;
  };
  const MapMock = React.forwardRef(function MapMock(props: MapMockProps, ref: React.Ref<unknown>) {
    React.useImperativeHandle(ref, () => ({ getMap: () => rmg.map }), []);
    rmg.renderCount += 1;
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
  const PopupMock = (props: { children?: ReactNode }) =>
    React.createElement('div', { 'data-testid': 'popup' }, props.children);
  return { default: MapMock, Popup: PopupMock };
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
  apiFetch: apiFetchMock,
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

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  hud.reset();
  rmg.loaded = false;
  rmg.map = makeMockMaplibreMap() as unknown;
  resetViewportSeq();
});

afterEach(() => {
  vi.useRealTimers();
});

function moveEvent(withGesture: boolean): Record<string, unknown> {
  return {
    viewState: { longitude: 116.4, latitude: 39.9, zoom: 5, bearing: 0, pitch: 0 },
    ...(withGesture ? { originalEvent: { type: 'pointermove' } } : {}),
  };
}

async function fireMoveAndSettle(evt: unknown): Promise<void> {
  act(() => {
    rmg.lastOnMove?.(evt);
  });
  await act(async () => {
    vi.advanceTimersByTime(150); // 100ms viewport debounce
  });
}

describe('MapPanel viewport 事件源（ADR-0227 / review P1-4）', () => {
  const panelProps = {
    layers: [],
    onRemoveLayer: () => undefined,
    onToggleLayer: () => undefined,
    onViewportChange: () => undefined,
  };

  it('用户手势（originalEvent 存在）→ settle 后 emit viewport 事件', async () => {
    render(<MapPanel {...panelProps} />);
    await fireMoveAndSettle(moveEvent(true));
    expect(emitViewportSettled).toHaveBeenCalledTimes(1);
    const call = vi.mocked(emitViewportSettled).mock.calls[0][0];
    expect(call.zoom).toBe(5);
    // F4 方序对账：viewportSeq 与 turn-start 快照（useMapBridge send 的
    // map_state.viewport_seq）同源同 tracker —— 落定事件携带落定方序，
    // 后端投影才能把 settled 事件与同轮 viewport 方序对账。
    expect(call.viewportSeq).toBe(viewportSeqTracker.current);
    expect(call.viewportSeq).toBeGreaterThanOrEqual(1);
  });

  it('程序性相机（agent flyTo，无 originalEvent）→ 不 emit 用户事件', async () => {
    render(<MapPanel {...panelProps} />);
    await fireMoveAndSettle(moveEvent(false));
    expect(emitViewportSettled).not.toHaveBeenCalled();
  });

  it('手势后紧跟程序性移动：debounce 尾态是程序性 → 不 emit（尾态判别）', async () => {
    render(<MapPanel {...panelProps} />);
    act(() => {
      rmg.lastOnMove?.(moveEvent(true));
    });
    await fireMoveAndSettle(moveEvent(false));
    expect(emitViewportSettled).not.toHaveBeenCalled();
  });
});
