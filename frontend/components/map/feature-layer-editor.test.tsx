/**
 * FeatureLayerEditor 拾取接线（G24 / P2）：
 * - MVT 层（HUD 行无内联要素）delete/vertex 首击走 pickAuthoritativeFeature
 *   权威链（瓦片几何不提交编辑）；命中 id = 服务端要素 id；
 * - inline 层保持本地精确命中（A01 不回退）；
 * - 权威拾取未命中/无稳定 id → 不提交（V04：无服务端写入）。
 * 编辑事务本身（preview→approve→undo）由 layer-edit-bridge.test.ts 锁定，
 * 这里 mock submitEditOps 只观察是否被调用及参数。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { useHudStore } from '@/lib/store/useHudStore';
import { resetSelectionStore } from '@/lib/selection/selection-store';

const pickMock = vi.hoisted(() => vi.fn());
vi.mock('@/lib/edit/mvt-pick', async (importOriginal) => {
  const orig = await importOriginal<typeof import('@/lib/edit/mvt-pick')>();
  return { ...orig, pickAuthoritativeFeature: pickMock };
});
const submitMock = vi.hoisted(() => vi.fn());
vi.mock('@/lib/edit/layer-edit-bridge', async (importOriginal) => {
  const orig = await importOriginal<typeof import('@/lib/edit/layer-edit-bridge')>();
  return {
    ...orig,
    submitEditOps: submitMock,
  };
});
vi.mock('@/lib/store/session-identity', () => ({
  getSessionIdentity: () => ({ sessionId: 'sess-editor-0001', ownerToken: 'tok' }),
}));
// 合同 effect 不得真实 fetch（否则渲染哪棵表单树取决于环境里 8001 端口）。
vi.mock('@/lib/api/field-contracts', () => ({
  bindingStatus: vi.fn(async () => ({ status: 'missing' })),
  getContract: vi.fn(),
}));
// 无 I18nProvider 时 useT 每次渲染返回新 t，pickEditTarget 身份抖动会让
// click effect 每次渲染 off/on 重注册，测试驱动到陈旧闭包；固定 t 消除该
// 测试环境特有效应。
vi.mock('@/lib/i18n/useT', () => {
  const t = (key: string) => key;
  return { useT: () => t };
});

import { FeatureLayerEditor } from './feature-layer-editor';
import type { MapRef } from 'react-map-gl/maplibre';

function fakeMap() {
  const draftSource = { setData: vi.fn() };
  return {
    on: vi.fn(),
    off: vi.fn(),
    getSource: vi.fn(() => draftSource),
    addSource: vi.fn(),
    getLayer: vi.fn(() => undefined),
    addLayer: vi.fn(),
    removeLayer: vi.fn(),
    removeSource: vi.fn(),
    project: vi.fn((p: [number, number]) => ({ x: p[0] * 100, y: -p[1] * 100 })),
    queryRenderedFeatures: vi.fn(() => []),
    getStyle: () => ({ layers: [] }),
    /** 稳定 draft 源：测试可读回顶点目标同步出的几何。 */
    draftSource,
  };
}

function clickHandlers(map: ReturnType<typeof fakeMap>) {
  const calls = map.on.mock.calls as Array<[string, unknown]>;
  const handler = calls.find(([ev]) => ev === 'click')?.[1];
  if (typeof handler !== 'function') throw new Error('click handler not registered');
  return { click: handler as (e: unknown) => void };
}

const MVT_LAYER = {
  id: 'ref:big-0001',
  name: '大图层',
  _refId: 'ref:big-0001',
  source: { type: 'FeatureCollection', features: [] },
  _descriptor: { mvt_capable: true, feature_count: 50000, content_revision: 7 },
  _tileUrl: 'http://x/layers/data/ref:big-0001/tiles/{z}/{x}/{y}.mvt?session_id=sess-editor-0001',
};
const INLINE_LAYER = {
  id: 'ref:small-0002',
  name: '小图层',
  _refId: 'ref:small-0002',
  source: {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      id: 11,
      geometry: { type: 'LineString', coordinates: [[104.0, 30.5], [105.0, 30.5]] },
      properties: { id: 11 },
    }],
  },
  _descriptor: { feature_count: 1, content_revision: 2 },
};

beforeEach(() => {
  cleanup();
  resetSelectionStore();
  pickMock.mockReset();
  submitMock.mockReset();
  submitMock.mockResolvedValue({ ok: true, planId: 'p1', revision: 3, createdWfids: [], retiredWfids: [] });
  useHudStore.getState().clearToolState();
  useHudStore.setState({ layers: [], editTarget: null, mapLoaded: true } as never);
});

describe('MVT 层编辑拾取走权威链（P2/A02/A03）', () => {
  it('delete_feature 首击：pickAuthoritativeFeature 命中 → 按权威 id 提交 delete', async () => {
    const map = fakeMap();
    const mapRef = { current: { getMap: () => map } } as unknown as React.RefObject<MapRef | null>;
    act(() => {
      useHudStore.setState({
        layers: [MVT_LAYER] as never,
        editTarget: { layerId: 'ref:big-0001', refId: 'ref:big-0001' },
        activeMapTool: 'delete_feature',
      } as never);
    });
    render(<FeatureLayerEditor mapRef={mapRef} />);
    const { click } = clickHandlers(map);
    expect(click).toBeTruthy();

    pickMock.mockResolvedValueOnce({
      id: 'wf1-abc',
      feature: { type: 'Feature', geometry: { type: 'Point', coordinates: [104.5, 30.5] }, properties: { _wfid: 'wf1-abc' } },
      distPx: 0,
    });
    await act(async () => {
      await click({ lngLat: { lng: 104.5, lat: 30.5 }, point: { x: 10450, y: -3050 } });
    });
    await waitFor(() => expect(submitMock).toHaveBeenCalledTimes(1));
    const [sid, refId, ops, opts] = submitMock.mock.calls[0];
    expect(sid).toBe('sess-editor-0001');
    expect(refId).toBe('ref:big-0001');
    expect(ops).toEqual([{ op: 'delete_features' }]);
    expect(opts.featureIds).toEqual(['wf1-abc']);
    expect(pickMock).toHaveBeenCalledTimes(1);
    expect(pickMock.mock.calls[0][0].refId).toBe('ref:big-0001');
    expect(pickMock.mock.calls[0][0].revision).toBe(7);
  });

  it('edit_vertices 首击：vertexTarget = 权威完整几何（不是瓦片碎片）', async () => {
    const map = fakeMap();
    const mapRef = { current: { getMap: () => map } } as unknown as React.RefObject<MapRef | null>;
    const authGeom = { type: 'LineString', coordinates: [[104.0, 30.5], [104.5, 30.5], [105.0, 30.5]] };
    act(() => {
      useHudStore.setState({
        layers: [MVT_LAYER] as never,
        editTarget: { layerId: 'ref:big-0001', refId: 'ref:big-0001' },
        activeMapTool: 'edit_vertices',
      } as never);
    });
    render(<FeatureLayerEditor mapRef={mapRef} />);
    const { click } = clickHandlers(map);
    pickMock.mockResolvedValueOnce({
      id: 'wf1-line',
      feature: { type: 'Feature', geometry: authGeom, properties: { _wfid: 'wf1-line' } },
      distPx: 2,
    });
    await act(async () => {
      await click({ lngLat: { lng: 104.5, lat: 30.5 }, point: { x: 10450, y: -3050 } });
    });
    expect(submitMock).not.toHaveBeenCalled(); // 只是选中顶点目标，不提交
    expect(pickMock.mock.calls[0][0].tolerancePx).toBe(6);
    // 不变量本体：vertexTarget 由权威完整几何驱动 —— 顶点源必须出现
    // authGeom 的全部 3 个顶点（瓦片碎片/未设置目标都不会有这个形状）。
    await waitFor(() => {
      const last = map.draftSource.setData.mock.calls.at(-1)?.[0] as {
        features: Array<{ geometry: { type: string; coordinates: unknown } }>;
      };
      const points = last.features.filter((f) => f.geometry.type === 'Point');
      expect(points.map((f) => f.geometry.coordinates)).toEqual(authGeom.coordinates);
    });
  });

  it('权威拾取返回 null geometry（合法 GeoJSON）→ 不设 vertexTarget，拖拽 handler 不解引用 null', async () => {
    const map = fakeMap();
    const mapRef = { current: { getMap: () => map } } as unknown as React.RefObject<MapRef | null>;
    act(() => {
      useHudStore.setState({
        layers: [MVT_LAYER] as never,
        editTarget: { layerId: 'ref:big-0001', refId: 'ref:big-0001' },
        activeMapTool: 'edit_vertices',
      } as never);
    });
    render(<FeatureLayerEditor mapRef={mapRef} />);
    const { click } = clickHandlers(map);
    // 修复前：geometry ?? null 存入 vertexTarget → 同步 effect 与 onDown 的
    // ringOf(null) 解引用 geometry.type 抛 TypeError。
    pickMock.mockResolvedValueOnce({
      id: 'wf1-null',
      feature: { type: 'Feature', geometry: null, properties: { _wfid: 'wf1-null' } },
      distPx: 0,
    });
    await act(async () => {
      await click({ lngLat: { lng: 104.5, lat: 30.5 }, point: { x: 10450, y: -3050 } });
    });
    expect(submitMock).not.toHaveBeenCalled();
    const calls = map.on.mock.calls as Array<[string, ...unknown[]]>;
    const onDown = calls.find(([ev]) => ev === 'mousedown')?.[2];
    expect(typeof onDown).toBe('function');
    expect(() => (onDown as (e: unknown) => void)({ point: { x: 0, y: 0 }, lngLat: { lng: 0, lat: 0 } })).not.toThrow();
  });

  it('权威拾取未命中 → 零提交（V04：无服务端写入）', async () => {
    const map = fakeMap();
    const mapRef = { current: { getMap: () => map } } as unknown as React.RefObject<MapRef | null>;
    act(() => {
      useHudStore.setState({
        layers: [MVT_LAYER] as never,
        editTarget: { layerId: 'ref:big-0001', refId: 'ref:big-0001' },
        activeMapTool: 'delete_feature',
      } as never);
    });
    render(<FeatureLayerEditor mapRef={mapRef} />);
    const { click } = clickHandlers(map);
    pickMock.mockResolvedValueOnce(null);
    await act(async () => {
      await click({ lngLat: { lng: 0, lat: 0 }, point: { x: 0, y: 0 } });
    });
    expect(submitMock).not.toHaveBeenCalled();
  });
});

describe('inline 层保持本地命中（A01 不回退）', () => {
  it('delete_feature 命中 inline 线要素中段（A09 语义）→ 不走 pickMock', async () => {
    const map = fakeMap();
    const mapRef = { current: { getMap: () => map } } as unknown as React.RefObject<MapRef | null>;
    act(() => {
      useHudStore.setState({
        layers: [INLINE_LAYER] as never,
        editTarget: { layerId: 'ref:small-0002', refId: 'ref:small-0002' },
        activeMapTool: 'delete_feature',
      } as never);
    });
    render(<FeatureLayerEditor mapRef={mapRef} />);
    const { click } = clickHandlers(map);
    // 线中段 (104.5, 30.5) —— 旧实现只测顶点会漏；新实现逐段距离命中
    await act(async () => {
      await click({ lngLat: { lng: 104.5, lat: 30.5 }, point: { x: 10450, y: -3050 } });
    });
    await waitFor(() => expect(submitMock).toHaveBeenCalledTimes(1));
    expect(submitMock.mock.calls[0][3].featureIds).toEqual(['11']);
    expect(pickMock).not.toHaveBeenCalled();
  });
});

describe('草稿预览同步（draft 代次进 effect 依赖）', () => {
  it('draw_line 逐点点击后草稿（线 + 顶点）同步进 draft 源，Escape 清空', async () => {
    const map = fakeMap();
    const draftSource = { setData: vi.fn() };
    // 稳定 source 引用：观察同步 effect 写入的 FeatureCollection。
    map.getSource.mockReturnValue(draftSource as never);
    const mapRef = { current: { getMap: () => map } } as unknown as React.RefObject<MapRef | null>;
    act(() => {
      useHudStore.setState({
        layers: [INLINE_LAYER] as never,
        editTarget: { layerId: 'ref:small-0002', refId: 'ref:small-0002' },
        activeMapTool: 'draw_line',
      } as never);
    });
    render(<FeatureLayerEditor mapRef={mapRef} />);
    const { click } = clickHandlers(map);

    // 修复前：bump() 只触发重渲染，同步 effect 依赖不含草稿代次 →
    // setData 停留在挂载时的空 FeatureCollection，草稿预览永不渲染。
    await act(async () => {
      await click({ lngLat: { lng: 104.0, lat: 30.5 }, point: { x: 10400, y: -3050 } });
    });
    await waitFor(() => expect(draftSource.setData).toHaveBeenCalled());
    const afterFirst = draftSource.setData.mock.calls.at(-1)?.[0] as { features: unknown[] };
    expect(afterFirst.features).toHaveLength(1); // 首点只有顶点，无线

    await act(async () => {
      await click({ lngLat: { lng: 105.0, lat: 30.5 }, point: { x: 10500, y: -3050 } });
    });
    await waitFor(() => {
      const last = draftSource.setData.mock.calls.at(-1)?.[0] as { features: unknown[] };
      expect(last.features).toHaveLength(3); // 1 条 LineString + 2 个顶点
    });
    const withDraft = draftSource.setData.mock.calls.at(-1)?.[0] as {
      features: Array<{ geometry: { type: string } }>;
    };
    expect(withDraft.features[0].geometry.type).toBe('LineString');

    // Escape 取消草稿（V05 零写入）—— 已渲染的陈旧草稿必须被清掉。
    // 派生在 body 上冒泡（真实按键 target 是聚焦元素，window 无 getAttribute）。
    act(() => {
      document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    await waitFor(() => {
      const last = draftSource.setData.mock.calls.at(-1)?.[0] as { features: unknown[] };
      expect(last.features).toHaveLength(0);
    });
    expect(submitMock).not.toHaveBeenCalled(); // 取消不落任何服务端写入
  });
});
