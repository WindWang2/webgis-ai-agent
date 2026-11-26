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
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
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
  // 源/层按 id 记账（getSource 在 addSource 前返回 undefined —— 与真实
  // maplibre 语义一致，共享顶点高亮层的挂载/摘除才能被断言）。
  const sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
  const layerIds = new Set<string>();
  return {
    on: vi.fn(),
    off: vi.fn(),
    getSource: vi.fn((id: string) => sources.get(id)),
    addSource: vi.fn((id: string) => { sources.set(id, { setData: vi.fn() }); }),
    getLayer: vi.fn((id: string) => (layerIds.has(id) ? ({ id } as unknown) : undefined)),
    addLayer: vi.fn((def: { id: string }) => { layerIds.add(def.id); }),
    removeLayer: vi.fn((id: string) => { layerIds.delete(id); }),
    removeSource: vi.fn((id: string) => { sources.delete(id); }),
    dragPan: { enable: vi.fn(), disable: vi.fn() },
    project: vi.fn((p: [number, number]) => ({ x: p[0] * 100, y: -p[1] * 100 })),
    queryRenderedFeatures: vi.fn(() => []),
    getStyle: () => ({ layers: [] }),
  };
}

function clickHandlers(map: ReturnType<typeof fakeMap>) {
  const calls = map.on.mock.calls as Array<[string, unknown]>;
  const handler = calls.find(([ev]) => ev === 'click')?.[1];
  if (typeof handler !== 'function') throw new Error('click handler not registered');
  return { click: handler as (e: unknown) => void };
}

/** 手势 handler 提取（maplibre on('mousedown', layerId, fn) 的尾参恒为回调）。 */
function gestureHandlers(map: ReturnType<typeof fakeMap>) {
  const calls = map.on.mock.calls as Array<[string, ...unknown[]]>;
  const last = (ev: string) => {
    const entry = calls.filter(([e]) => e === ev).pop();
    return entry?.[entry.length - 1] as ((...args: unknown[]) => unknown) | undefined;
  };
  return { down: last('mousedown'), move: last('mousemove'), up: last('mouseup'), click: last('click') };
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

// ── ADR-0304 共享顶点拖拽（move_shared_vertex；本地检测只是临时预览） ──

/** 两条线共享顶点 (104.0, 30.5) 的 inline 图层。 */
const SHARED_LAYER = {
  id: 'ref:shared-0003',
  name: '共享图层',
  _refId: 'ref:shared-0003',
  source: {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        id: 21,
        geometry: { type: 'LineString', coordinates: [[104.0, 30.5], [105.0, 30.5]] },
        properties: { id: 21 },
      },
      {
        type: 'Feature',
        id: 22,
        geometry: { type: 'LineString', coordinates: [[104.0, 30.5], [104.0, 31.5]] },
        properties: { id: 22 },
      },
    ],
  },
  _descriptor: { feature_count: 2, content_revision: 2 },
};

/** edit_vertices 手势流：首击选目标 → mousedown 抓顶点 → mousemove → mouseup。 */
async function dragVertex(
  map: ReturnType<typeof fakeMap>,
  vertex: [number, number],
  to: [number, number],
) {
  const h = gestureHandlers(map);
  const at = { x: vertex[0] * 100, y: -vertex[1] * 100 };
  await act(async () => {
    await h.click?.({ lngLat: { lng: vertex[0], lat: vertex[1] }, point: at });
  });
  await act(async () => {
    h.down?.({ lngLat: { lng: vertex[0], lat: vertex[1] }, point: at });
  });
  await act(async () => {
    h.move?.({ lngLat: { lng: to[0], lat: to[1] }, point: { x: to[0] * 100, y: -to[1] * 100 } });
  });
  await act(async () => {
    await h.up?.();
  });
}

function renderVertexEditor(map: ReturnType<typeof fakeMap>, layer: typeof SHARED_LAYER) {
  const mapRef = { current: { getMap: () => map } } as unknown as React.RefObject<MapRef | null>;
  act(() => {
    useHudStore.setState({
      layers: [layer] as never,
      editTarget: { layerId: layer.id, refId: layer._refId },
      activeMapTool: 'edit_vertices',
    } as never);
  });
  render(<FeatureLayerEditor mapRef={mapRef} />);
}

describe('共享顶点拖拽（ADR-0304）', () => {
  it('共享顶点拖拽提交 move_shared_vertex（at/to 坐标 + 原目标要素 id）', async () => {
    const map = fakeMap();
    renderVertexEditor(map, SHARED_LAYER);
    await dragVertex(map, [104.0, 30.5], [104.2, 30.6]);
    await waitFor(() => expect(submitMock).toHaveBeenCalledTimes(1));
    const [sid, refId, ops, opts] = submitMock.mock.calls[0];
    expect(sid).toBe('sess-editor-0001');
    expect(refId).toBe('ref:shared-0003');
    expect(ops).toEqual([{ op: 'move_shared_vertex', at: [104.0, 30.5], to: [104.2, 30.6] }]);
    expect(opts.featureIds).toEqual(['21']);
    expect(opts.labelKey).toBe('topologyEditing.sharedVertexMode.title');
  });

  it('共享拖拽期间邻接要素高亮层挂载（wb-shared-hl-*，只含邻接要素）', async () => {
    const map = fakeMap();
    renderVertexEditor(map, SHARED_LAYER);
    const h = gestureHandlers(map);
    await act(async () => {
      await h.click?.({ lngLat: { lng: 104.0, lat: 30.5 }, point: { x: 10400, y: -3050 } });
    });
    await act(async () => {
      h.down?.({ lngLat: { lng: 104.0, lat: 30.5 }, point: { x: 10400, y: -3050 } });
    });
    await waitFor(() => expect(map.addLayer).toHaveBeenCalledWith(expect.objectContaining({ id: 'wb-shared-hl-fill' })));
    expect(map.addLayer).toHaveBeenCalledWith(expect.objectContaining({ id: 'wb-shared-hl-line' }));
    expect(map.addSource).toHaveBeenCalledWith('wb-shared-hl-src', expect.anything());
    const hl = map.getSource('wb-shared-hl-src') as { setData: ReturnType<typeof vi.fn> };
    const data = hl.setData.mock.calls[0][0] as { features: Array<{ id: number }> };
    expect(data.features).toHaveLength(1);
    expect(data.features[0].id).toBe(22);
    // 松开 → 高亮摘除（服务端预览才是权威传播面）
    await act(async () => {
      h.move?.({ lngLat: { lng: 104.2, lat: 30.6 }, point: { x: 10420, y: -3060 } });
    });
    await act(async () => {
      await h.up?.();
    });
    await waitFor(() => expect(map.getLayer('wb-shared-hl-fill')).toBeUndefined());
    expect(map.getSource('wb-shared-hl-src')).toBeUndefined();
  });

  it('非共享顶点拖拽保持 replace_geometry（行为字节级不变，无高亮）', async () => {
    const map = fakeMap();
    renderVertexEditor(map, INLINE_LAYER);
    await dragVertex(map, [105.0, 30.5], [105.5, 30.5]);
    await waitFor(() => expect(submitMock).toHaveBeenCalledTimes(1));
    const [, , ops] = submitMock.mock.calls[0];
    expect(ops).toEqual([{
      op: 'replace_geometry',
      geometry: { type: 'LineString', coordinates: [[104.0, 30.5], [105.5, 30.5]] },
    }]);
    expect(map.addSource).not.toHaveBeenCalledWith('wb-shared-hl-src', expect.anything());
    expect(map.addLayer).not.toHaveBeenCalledWith(expect.objectContaining({ id: 'wb-shared-hl-fill' }));
  });

  it('共享拖拽中 Escape 取消：零服务端写入 + 高亮摘除', async () => {
    const map = fakeMap();
    renderVertexEditor(map, SHARED_LAYER);
    const h = gestureHandlers(map);
    await act(async () => {
      await h.click?.({ lngLat: { lng: 104.0, lat: 30.5 }, point: { x: 10400, y: -3050 } });
    });
    await act(async () => {
      h.down?.({ lngLat: { lng: 104.0, lat: 30.5 }, point: { x: 10400, y: -3050 } });
    });
    await waitFor(() => expect(map.getLayer('wb-shared-hl-fill')).toBeTruthy());
    // 与真实焦点语义一致：keydown 从 body 冒泡（window capture 捕获）
    await act(async () => {
      fireEvent.keyDown(document.body, { key: 'Escape' });
    });
    // 松开鼠标（Escape 后 onUp 必须提前返回）
    await act(async () => {
      h.move?.({ lngLat: { lng: 104.2, lat: 30.6 }, point: { x: 10420, y: -3060 } });
    });
    await act(async () => {
      await h.up?.();
    });
    expect(submitMock).not.toHaveBeenCalled();
    expect(map.getLayer('wb-shared-hl-fill')).toBeUndefined();
    expect(map.getSource('wb-shared-hl-src')).toBeUndefined();
    expect(map.dragPan.enable).toHaveBeenCalled();
  });
});
