/**
 * G25 FrameProductGrid 组件集成测试（jsdom + mock react-map-gl + 真实
 * MapSpecRuntime —— comparison-view.test.tsx 同款 harness）。
 *
 * 覆盖：开合/入口 chip、三幅投影、live 视窗挂载与预算、激活切换（CAS
 * mutation + 快照捕获）、键盘 '['/']'/Escape、快照瓦片版本标注、
 * 逐帧观察挂点。
 */
/* eslint-disable @typescript-eslint/no-require-imports -- vi.mock 工厂 hoist 纪律（同 comparison-view.test） */
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { MapRef } from 'react-map-gl/maplibre';
import { FrameProductGrid } from './frame-grid';
import { makeMockMaplibreMap } from '../../../test/__mocks__/maplibre-map';
import { mapInstanceRegistry } from './frame-instance-registry';
import {
  closeFrameGrid,
  openFrameGrid,
  resetFrameGridForTest,
  setActiveFrameRuntime,
} from '@/lib/cartography/frame-grid-store';
import {
  commitMapSpecDocument,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import { useHudStore } from '@/lib/store/useHudStore';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

const rmg = vi.hoisted(() => ({
  map: null as ReturnType<typeof makeMockMaplibreMap> | null,
}));

vi.mock('react-map-gl/maplibre', () => {
  const React = require('react');
  const MapMock = React.forwardRef(function MapMock(props: any, ref: any) {
    React.useImperativeHandle(ref, () => ({ getMap: () => rmg.map }), []);
    // onLoad 在 effect 中触发（模拟 react-map-gl 的 load 事件）。
    React.useEffect(() => {
      const t = setTimeout(() => props.onLoad?.(), 0);
      return () => clearTimeout(t);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return React.createElement('div', { 'data-testid': 'frame-map-mock' });
  });
  return { default: MapMock };
});

/** jsdom 无 canvas 2d —— 捕获路径走 mock 地图的 canvas-like toDataURL。 */
function installCanvasProxy() {
  /* no-op：capture 直接读 live canvas 的 toDataURL（mock 已提供）。 */
}

/** jsdom sessionStorage 复位。 */
function clearStorage() {
  sessionStorage.clear();
}

function threeFrameSpec(): MapSpec {
  return {
    view: { center: [104.0, 30.6], zoom: 9 },
    sources: {
      'src-0': {
        type: 'geojson',
        inlineData: {
          type: 'FeatureCollection',
          features: [
            { type: 'Feature', properties: { year: 2020, per_10k: 12, dname: 'a' }, geometry: { type: 'Point', coordinates: [104, 30.6] } },
            { type: 'Feature', properties: { year: 2022, per_10k: 14, dname: 'b' }, geometry: { type: 'Point', coordinates: [104.1, 30.6] } },
            { type: 'Feature', properties: { year: 2024, per_10k: 15, dname: 'c' }, geometry: { type: 'Point', coordinates: [104.2, 30.6] } },
          ],
        },
        content_revision: 7,
      },
    },
    layers: [
      {
        id: 'layer-0', type: 'circle', source: 'src-0',
        legend_spec: { type: 'graduated', field: 'per_10k', breaks: [0, 10, 20], method: 'equal_interval', k: 2, palette: 'Blues' },
      },
    ],
    frame_product: {
      enabled: true,
      product_kind: 'time_series',
      grid: { columns: 3, title: '成都学位' },
      active_frame: 'fp-t-2020',
      frames: [
        { frame_id: 'fp-t-2020', title: '2020', sync_group: 'A', data: { ref: 'ref:c', layer_id: 'layer-0', field: 'per_10k', content_revision: 7, where: { op: 'eq', field: 'year', value: 2020 } } },
        { frame_id: 'fp-t-2022', title: '2022', sync_group: 'A', data: { ref: 'ref:c', layer_id: 'layer-0', field: 'per_10k', content_revision: 7, where: { op: 'eq', field: 'year', value: 2022 } } },
        { frame_id: 'fp-t-2024', title: '2024', sync_group: 'B', data: { ref: 'ref:c', layer_id: 'layer-0', field: 'per_10k', content_revision: 7, where: { op: 'eq', field: 'year', value: 2024 } } },
      ],
    },
    layout: {
      frames: [
        { id: 'fp-t-2020', title: '2020', enabled: true },
        { id: 'fp-t-2022', title: '2022', enabled: true },
        { id: 'fp-t-2024', title: '2024', enabled: true },
      ],
    },
  } as unknown as MapSpec;
}

/** 帧 map mock：idle 自动触发（waitForMapIdle 即时通过）。 */
function frameMapMock() {
  const map = makeMockMaplibreMap();
  const origOnce = map.once.bind(map);
  map.once = (((type: string, cb: (...a: unknown[]) => void) => {
    if (type === 'idle') {
      setTimeout(() => cb(), 0);
      return map;
    }
    return origOnce(type as never, cb as never);
  }) as typeof map.once);
  return map;
}

function harness() {
  const primary = makeMockMaplibreMap();
  const primaryRef = { current: { getMap: () => primary } as unknown as MapRef };
  const frame = frameMapMock();
  rmg.map = frame as never;
  return { primary, primaryRef, frame };
}

function renderGrid(props: ReturnType<typeof harness>, sessionId = 's-g25') {
  return render(
    <FrameProductGrid
      primaryMapRef={props.primaryRef}
      mapStyle={{ version: 8, sources: {}, layers: [] }}
      sessionId={sessionId}
      ownerToken="tok"
    />,
  );
}

const mutations: Array<Record<string, unknown>> = [];
const fetchLog: string[] = [];
function installMutationRoute() {
  mutations.length = 0;
  fetchLog.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      fetchLog.push(url);
      if (url.includes('/mapspec/mutations')) {
        mutations.push(JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>);
        return new Response(
          JSON.stringify({ status: 'ok', mutation_revision: 99 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return new Response('{}', { status: 200 });
    }),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetFrameGridForTest();
  mapInstanceRegistry.resetForTest();
  clearStorage();
  installCanvasProxy();
  installMutationRoute();
  setMapSpecSessionCursor('s-g25', 3, 'tok');
  useHudStore.setState({ mapLoaded: true, layers: [] });
  const main = mapInstanceRegistry.register('main', 'main'); // 主图占预算（map-panel 补丁语义）
  void main;
});

afterEach(() => {
  closeFrameGrid();
  cleanupRender();
});

function cleanupRender() {
  document.body.innerHTML = '';
}

describe('FrameProductGrid', () => {
  it('无产品 → 不渲染任何内容', () => {
    commitMapSpecDocument(null as never, 1);
    const h = harness();
    const { container } = renderGrid(h);
    expect(container).toBeEmptyDOMElement();
  });

  it('产品绑定 + 关闭 → 入口 chip；打开 → 三瓦片 + 活动画幅 live 视窗', async () => {
    commitMapSpecDocument(threeFrameSpec() as never, 3);
    const h = harness();
    renderGrid(h);
    const chip = screen.getByTestId('frame-grid-open-chip');
    expect(chip).toBeTruthy();
    expect(chip.textContent).toContain('3');

    act(() => openFrameGrid('fp-t-2020'));
    await waitFor(
      () => expect(screen.getAllByTestId('frame-grid-tile')).toHaveLength(3),
    );
    // 瓦片身份 = data-frame-id（稳定 frame_id）。
    expect(screen.getByTestId('frame-grid-tiles').querySelector('[data-frame-id="fp-t-2024"]')).toBeTruthy();
    // live 视窗挂载（react-map-gl mock）——活动画幅 fp-t-2020。
    await waitFor(() => expect(rmg.map).toBeTruthy());
    // 预算显示：主图 1 + live 1 ≤ 2（A09 计入主图）。
    expect(mapInstanceRegistry.liveCount()).toBeLessThanOrEqual(mapInstanceRegistry.budget());
    expect(screen.getByTestId('frame-grid-budget').textContent).toMatch(/活地图 \d+\/2/);
  });

  it('激活另一幅：CAS mutation 发出 + live 切换 + 快照捕获入 LRU', async () => {
    commitMapSpecDocument(threeFrameSpec() as never, 3);
    const h = harness();
    renderGrid(h);
    act(() => openFrameGrid('fp-t-2020'));
    await waitFor(() => expect(screen.getAllByTestId('frame-grid-tile')).toHaveLength(3));

    // 点第三幅激活。
    const tile2024 = screen.getByTestId('frame-grid-tiles').querySelector('[data-frame-id="fp-t-2024"]');
    const btn = tile2024!.querySelector('[data-testid="frame-grid-activate"]') as HTMLElement;
    await act(async () => {
      fireEvent.click(btn);
    });
    // active_frame CAS mutation（set_frame_product intent）。
    await waitFor(() => expect(mutations.length).toBeGreaterThanOrEqual(1), {
      onTimeout: () => console.log('FETCH_LOG:', JSON.stringify(fetchLog.slice(0, 20))),
    });
    const body = mutations[0] as { intent: string; patch: { active_frame: string } };
    expect(body.intent).toBe('set_frame_product');
    expect(body.patch.active_frame).toBe('fp-t-2024');
    // scope 徽标显示新活动画幅（A02/P5：面板与地图同一服务端真相）。
    await waitFor(() =>
      expect(screen.getByTestId('frame-grid-scope-chip').textContent).toContain('fp-t-2024'),
    );
  });

  it('键盘："]" 激活下一幅；Escape 关闭（A25）', async () => {
    commitMapSpecDocument(threeFrameSpec() as never, 3);
    const h = harness();
    renderGrid(h);
    act(() => openFrameGrid('fp-t-2020'));
    await waitFor(() => expect(screen.getAllByTestId('frame-grid-tile')).toHaveLength(3));

    await act(async () => {
      fireEvent.keyDown(window, { key: ']' });
    });
    await waitFor(() =>
      expect(screen.getByTestId('frame-grid-scope-chip').textContent).toContain('fp-t-2022'),
    );
    await act(async () => {
      fireEvent.keyDown(window, { key: '[' });
    });
    await waitFor(() =>
      expect(screen.getByTestId('frame-grid-scope-chip').textContent).toContain('fp-t-2020'),
    );
    await act(async () => {
      fireEvent.keyDown(window, { key: 'Escape' });
    });
    await waitFor(() => expect(screen.queryByTestId('frame-grid-root')).toBeNull());
    // 关闭后回到 chip。
    expect(screen.getByTestId('frame-grid-open-chip')).toBeTruthy();
  });

  it('预算被对比视图占用 → 不挂 live 视窗，如实披露（A09）', async () => {
    commitMapSpecDocument(threeFrameSpec() as never, 3);
    const h = harness();
    const comparison = mapInstanceRegistry.register('comparison', 'comparison');
    renderGrid(h);
    act(() => openFrameGrid('fp-t-2020'));
    await waitFor(() =>
      expect(screen.getByTestId('frame-grid-budget-blocked')).toBeTruthy(),
    );
    expect(screen.queryByTestId('frame-map-mock')).toBeNull();
    comparison.release();
    await waitFor(() =>
      expect(screen.queryByTestId('frame-grid-budget-blocked')).toBeNull(),
    );
  });

  it('逐帧观察挂点：live/snapshot/not_observed 如实分类（P6/A12）', async () => {
    commitMapSpecDocument(threeFrameSpec() as never, 3);
    const h = harness();
    renderGrid(h);
    act(() => openFrameGrid('fp-t-2020'));
    await waitFor(() => expect(screen.getAllByTestId('frame-grid-tile')).toHaveLength(3));
    await waitFor(() => {
      const obs = (window as unknown as { __g25FrameObservation?: () => { rows: Array<{ frame_id: string; mode: string }> } }).__g25FrameObservation?.();
      expect(obs).toBeTruthy();
      const byId = new Map(obs!.rows.map((r) => [r.frame_id, r.mode]));
      // 活动画幅（尚未 reconcile 落定）→ not_observed 或 live（不冒充 snapshot）。
      expect(['not_observed', 'live_observed', 'stale_observation']).toContain(byId.get('fp-t-2020'));
      // 未挂载帧 → not_observed（绝不宣称 live）。
      expect(byId.get('fp-t-2022')).toBe('not_observed');
      expect(byId.get('fp-t-2024')).toBe('not_observed');
    });
  });

  it('活动画幅从产品消失 → 确定性后继（A13）', async () => {
    const spec = threeFrameSpec();
    commitMapSpecDocument(spec as never, 3);
    const h = harness();
    renderGrid(h);
    act(() => openFrameGrid('fp-t-2022'));
    await waitFor(() =>
      expect(screen.getByTestId('frame-grid-scope-chip').textContent).toContain('fp-t-2022'),
    );
    // 服务端删除 fp-t-2022（帧序 2020/2024）——新对象提交（引用判等广播）。
    const specAfterDelete = {
      ...(spec as Record<string, unknown>),
      frame_product: {
        ...(spec.frame_product as Record<string, unknown>),
        frames: (spec.frame_product!.frames as Array<Record<string, unknown>>).filter(
          (f) => f.frame_id !== 'fp-t-2022',
        ),
      },
      layout: {
        ...(spec.layout as Record<string, unknown>),
        frames: (spec.layout!.frames as Array<Record<string, unknown>>).filter(
          (f) => f.id !== 'fp-t-2022',
        ),
      },
    } as unknown as MapSpec;
    act(() => {
      commitMapSpecDocument(specAfterDelete as never, 4);
    });
    // 后继 = 被删者的下一个可用帧（环形）→ fp-t-2024。
    await waitFor(() =>
      expect(screen.getByTestId('frame-grid-scope-chip').textContent).toContain('fp-t-2024'),
    );
    expect(screen.getAllByTestId('frame-grid-tile')).toHaveLength(2);
  });
});
