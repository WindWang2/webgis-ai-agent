/**
 * FeatureLayerEditor 拾取接线（G24 / P2）：
 * - MVT 层（HUD 行无内联要素）delete/vertex 首击走 pickAuthoritativeFeature
 *   权威链（瓦片几何不提交编辑）；命中 id = 服务端要素 id；
 * - inline 层保持本地精确命中（A01 不回退）；
 * - 权威拾取未命中/无稳定 id → 不提交（V04：无服务端写入）。
 * 编辑事务本身（preview→approve→undo）由 layer-edit-bridge.test.ts 锁定，
 * 这里 mock submitEditOps 只观察是否被调用及参数。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

import { FeatureLayerEditor } from './feature-layer-editor';
import type { MapRef } from 'react-map-gl/maplibre';

function fakeMap() {
  return {
    on: vi.fn(),
    off: vi.fn(),
    getSource: vi.fn(() => ({ setData: vi.fn() })),
    addSource: vi.fn(),
    getLayer: vi.fn(() => undefined),
    addLayer: vi.fn(),
    removeLayer: vi.fn(),
    removeSource: vi.fn(),
    project: vi.fn((p: [number, number]) => ({ x: p[0] * 100, y: -p[1] * 100 })),
    queryRenderedFeatures: vi.fn(() => []),
    getStyle: () => ({ layers: [] }),
  };
}

function clickHandlers(map: ReturnType<typeof fakeMap>) {
  const calls = map.on.mock.calls as Array<[string, unknown]>;
  const click = calls.find(([ev]) => ev === 'click')?.[1] as ((e: unknown) => void) | undefined;
  return { click };
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
afterEach(() => {
  useHudStore.getState().clearToolState();
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
