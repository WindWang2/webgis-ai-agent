/**
 * Wave 8 对比工作区 · ComparisonView 组件冒烟/交互测试。
 *
 * House style（对齐 map-panel.test.tsx）：手写 react-map-gl mock（副图 ref
 * 指向共享 makeMockMaplibreMap 实例），HUD store 用**真实** useHudStore
 * （workbenchSlice 的 comparison 动作原样驱动 —— 与 slice 测试同源真相），
 * 制图真相用真实 session-cursor（commitMapSpecDocument 提交后验证副图
 * 只挂载副图层族 —— MapSpecRuntime/renderer 真跑）。
 */
/* eslint-disable @typescript-eslint/no-require-imports --
 * vi.mock 工厂被 vitest hoist 到顶层 import 之上，引用模块级变量会 TDZ 报错，
 * 只能在工厂内 require（vitest 官方模式）；仅本测试文件适用。 */
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ComparisonView } from './comparison-view';
import { useHudStore } from '@/lib/store/useHudStore';
import { makeMockMaplibreMap } from '../../../test/__mocks__/maplibre-map';
import {
  commitMapSpecDocument,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import type { MapRef } from 'react-map-gl/maplibre';

const rmg = vi.hoisted(() => ({
  lastProps: null as Record<string, any> | null,
  map: null as any,
}));

vi.mock('react-map-gl/maplibre', () => {
  const React = require('react');
  const MapMock = React.forwardRef(function MapMock(props: any, ref: any) {
    React.useImperativeHandle(ref, () => ({ getMap: () => rmg.map }), []);
    rmg.lastProps = props;
    return React.createElement('div', { 'data-testid': 'comparison-map-mock' });
  });
  return { default: MapMock };
});

const EMPTY_COMPARISON = {
  active: false,
  kind: 'swipe' as const,
  primaryLayerId: null,
  secondaryLayerId: null,
  syncPan: true,
  syncZoom: true,
  position: 0.5,
};

function resetComparisonState() {
  useHudStore.setState({ comparison: { ...EMPTY_COMPARISON } });
}

function harness(overrides: { primary?: any; secondary?: any } = {}) {
  const primary = overrides.primary ?? makeMockMaplibreMap();
  const secondary = overrides.secondary ?? makeMockMaplibreMap();
  rmg.map = secondary;
  const primaryRef = { current: { getMap: () => primary } as unknown as MapRef };
  return { primary, secondary, primaryRef };
}

const STUB_STYLE = { version: 8 as const, sources: {}, layers: [] };

beforeEach(() => {
  vi.clearAllMocks();
  resetComparisonState();
  useHudStore.setState({ mapLoaded: true });
  // 制图真相复位（committed spec / pending 都是模块级单例）。
  setMapSpecSessionCursor(undefined);
});

describe('ComparisonView · 渲染门', () => {
  it('comparison.active=false 时不渲染任何覆盖层', () => {
    const { primaryRef } = harness();
    const { container } = render(
      <ComparisonView primaryMapRef={primaryRef} mapStyle={STUB_STYLE} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('active + kind=swipe：渲染覆盖层/副图/分割把手，clip-path 跟随 position', () => {
    const { primaryRef } = harness();
    useHudStore.getState().enterComparison({ primaryLayerId: 'A', secondaryLayerId: 'B' });
    useHudStore.getState().updateComparison({ position: 0.4 });
    render(<ComparisonView primaryMapRef={primaryRef} mapStyle={STUB_STYLE} />);

    expect(screen.getByTestId('comparison-overlay')).toBeInTheDocument();
    expect(screen.getByTestId('comparison-secondary-map')).toBeInTheDocument();
    expect(screen.getByTestId('comparison-map-mock')).toBeInTheDocument();

    const divider = screen.getByTestId('comparison-divider');
    expect(divider).toHaveAttribute('role', 'slider');
    expect(divider).toHaveAttribute('aria-valuenow', '0.4');
    // jsdom 的 toHaveStyle 会做 0→0px 归一化差异，这里直接断言内联 clip-path。
    expect(screen.getByTestId('comparison-secondary-map').style.clipPath).toBe(
      'inset(0 0 0 40%)',
    );
    // 副图拿到的底图样式与主图同源（样式 parity 契约）
    expect(rmg.lastProps?.mapStyle).toEqual(STUB_STYLE);
  });
});

describe('ComparisonView · 分割把手', () => {
  it('ArrowRight/ArrowLeft 以 ±0.02 步进写回 store（键盘可达）', () => {
    const { primaryRef } = harness();
    useHudStore.getState().enterComparison({ primaryLayerId: 'A', secondaryLayerId: 'B' });
    render(<ComparisonView primaryMapRef={primaryRef} mapStyle={STUB_STYLE} />);

    const divider = screen.getByTestId('comparison-divider');
    fireEvent.keyDown(divider, { key: 'ArrowRight' });
    expect(useHudStore.getState().comparison.position).toBeCloseTo(0.52, 10);
    fireEvent.keyDown(divider, { key: 'ArrowLeft' });
    expect(useHudStore.getState().comparison.position).toBeCloseTo(0.5, 10);
    // 两端键：Home/End 直接到边界
    fireEvent.keyDown(divider, { key: 'End' });
    expect(useHudStore.getState().comparison.position).toBe(1);
    fireEvent.keyDown(divider, { key: 'Home' });
    expect(useHudStore.getState().comparison.position).toBe(0);
  });
});

describe('ComparisonView · 退出', () => {
  it('退出按钮走 exitComparison（store 回到未激活 + 覆盖层卸载）', () => {
    const { primaryRef } = harness();
    useHudStore.getState().enterComparison({ primaryLayerId: 'A', secondaryLayerId: 'B' });
    const { queryByTestId } = render(
      <ComparisonView primaryMapRef={primaryRef} mapStyle={STUB_STYLE} />,
    );
    expect(queryByTestId('comparison-overlay')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('comparison-exit'));
    expect(useHudStore.getState().comparison.active).toBe(false);
    expect(queryByTestId('comparison-overlay')).not.toBeInTheDocument();
  });
});

describe('ComparisonView · kind 切换', () => {
  it('side-by-side 已诚实下线：UI 无切换按钮，进入后仍按 50% 裁剪渲染', () => {
    // Review R1（GIS F2 CRITICAL）：双半屏在「主图不动」约束下两图层永不
    // 共地理 —— 不构成对比。词表保留（状态机/裁剪语义不变），UI 只暴露滑动。
    const { primaryRef } = harness();
    useHudStore.getState().enterComparison({
      primaryLayerId: 'A',
      secondaryLayerId: 'B',
      kind: 'side-by-side',
    });
    render(<ComparisonView primaryMapRef={primaryRef} mapStyle={STUB_STYLE} />);

    expect(screen.queryByTestId('comparison-divider')).not.toBeInTheDocument();
    expect(screen.getByTestId('comparison-secondary-map').style.clipPath).toBe(
      'inset(0 0 0 50%)',
    );
    // UI 不提供 side-by-side 入口（诚实 UI）。
    expect(screen.queryByRole('button', { name: '并排' })).toBeNull();
  });
});

describe('ComparisonView · 相机同步', () => {
  it('副图 move → 主图 jumpTo（全同步：center/zoom；bearing/pitch 已一致则省略）', () => {
    const primary = makeMockMaplibreMap({ center: [116.4, 39.9], zoom: 4 });
    const secondary = makeMockMaplibreMap({ center: [121.47, 31.23], zoom: 10 });
    const { primaryRef } = harness({ primary, secondary });
    useHudStore.getState().enterComparison({ primaryLayerId: 'A', secondaryLayerId: 'B' });
    render(<ComparisonView primaryMapRef={primaryRef} mapStyle={STUB_STYLE} />);

    act(() => {
      rmg.lastProps?.onMove({
        viewState: { longitude: 121.47, latitude: 31.23, zoom: 10, bearing: 0, pitch: 0 },
      });
    });
    expect(primary.jumpTo).toHaveBeenCalledWith({ center: [121.47, 31.23], zoom: 10 });
  });

  it('syncZoom=false 时补丁不含 zoom', () => {
    const primary = makeMockMaplibreMap({ center: [116.4, 39.9], zoom: 4 });
    const secondary = makeMockMaplibreMap({ center: [121.47, 31.23], zoom: 10 });
    const { primaryRef } = harness({ primary, secondary });
    useHudStore
      .getState()
      .enterComparison({ primaryLayerId: 'A', secondaryLayerId: 'B', syncZoom: false });
    render(<ComparisonView primaryMapRef={primaryRef} mapStyle={STUB_STYLE} />);

    act(() => {
      rmg.lastProps?.onMove({
        viewState: { longitude: 121.47, latitude: 31.23, zoom: 10, bearing: 0, pitch: 0 },
      });
    });
    expect(primary.jumpTo).toHaveBeenCalledWith({ center: [121.47, 31.23] });
  });

  it('主图 move → 副图 jumpTo（主图实例事件通道）', () => {
    const primary = makeMockMaplibreMap({ center: [116.4, 39.9], zoom: 8 });
    const secondary = makeMockMaplibreMap({ center: [121.47, 31.23], zoom: 10 });
    const { primaryRef } = harness({ primary, secondary });
    useHudStore.getState().enterComparison({ primaryLayerId: 'A', secondaryLayerId: 'B' });
    render(<ComparisonView primaryMapRef={primaryRef} mapStyle={STUB_STYLE} />);

    act(() => {
      primary._fire('move');
    });
    expect(secondary.jumpTo).toHaveBeenCalledWith({ center: [116.4, 39.9], zoom: 8 });
  });
});

describe('ComparisonView · 样式 parity（副图只挂副图层族）', () => {
  it('committed spec 的 A/B 两族 → 副图只挂 B 族层与 B 源', async () => {
    commitMapSpecDocument({
      version: '1',
      sources: {
        A: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } },
        B: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } },
      },
      layers: [
        { id: 'A__fill', source: 'A', type: 'fill' },
        { id: 'B__fill', source: 'B', type: 'circle' },
      ],
    });
    const secondary = makeMockMaplibreMap();
    const { primaryRef } = harness({ secondary });
    useHudStore.getState().enterComparison({ primaryLayerId: 'A', secondaryLayerId: 'B' });
    render(<ComparisonView primaryMapRef={primaryRef} mapStyle={STUB_STYLE} />);

    // 副图 onLoad → ready → MapSpecRuntime reconcile（异步 diff）落定
    await act(async () => {
      rmg.lastProps?.onLoad?.();
    });
    await waitFor(() => {
      expect(secondary._calls.addLayer.some((c: any) => c.def.id === 'B__fill')).toBe(true);
    });
    const addedIds = secondary._calls.addLayer.map((c: any) => c.def.id);
    expect(addedIds).not.toContain('A__fill');
    // 源过滤：只有被副图层引用的源进入副图
    expect(secondary._sources.B).toBeTruthy();
    expect(secondary._sources.A).toBeUndefined();
  });
});

describe('ComparisonView · 层名投影', () => {
  it('控制条展示主/副图层名（HUD 行按族 id 反查）', () => {
    useHudStore.setState({
      layers: [
        {
          id: 'row-a',
          name: '六环人口',
          type: 'vector',
          visible: true,
          opacity: 1,
          _mapspecLayerId: 'A',
        } as any,
      ],
    });
    const { primaryRef } = harness();
    useHudStore.getState().enterComparison({ primaryLayerId: 'A', secondaryLayerId: 'B' });
    render(<ComparisonView primaryMapRef={primaryRef} mapStyle={STUB_STYLE} />);

    expect(screen.getByText('六环人口')).toBeInTheDocument();
    expect(screen.getByText('B')).toBeInTheDocument(); // 无 HUD 行时如实回退族 id
  });
});
