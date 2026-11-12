/**
 * mvt-pick（G24/P2）域内契约：
 * - 候选 id 去重（跨子层/跨瓦片/多世界副本同 id 只取一次；A04/A05）；
 * - 权威回查路径：链内 404 + 数字 id → positional 重试（带 v guard）；
 * - _wfid 优先链；无稳定 id 候选不出；
 * - AbortSignal 取消后不再回查（A17 取消语义）；
 * - pickAuthoritativeFeature：瓦片几何不参与命中判定 —— 命中只认服务端
 *   返回的完整几何（A03）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Map as MaplibreMap } from 'maplibre-gl';

const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/transport', () => ({
  apiFetch: apiFetchMock,
}));
vi.mock('@/lib/store/session-identity', () => ({
  getSessionIdentity: () => ({ sessionId: 'sess-mvt-pick-0001', ownerToken: 'tok' }),
}));

import {
  MAX_PICK_CANDIDATES,
  candidateIdsFromHits,
  fetchAuthoritativeFeature,
  pickAuthoritativeFeature,
} from '@/lib/edit/mvt-pick';

beforeEach(() => {
  apiFetchMock.mockReset();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe('candidateIdsFromHits', () => {
  it('A04/A05：跨子层/多副本同 id 去重；_wfid 优先', () => {
    const hits = [
      { id: 7, properties: { _wfid: 'wf1-a', OBJECTID: 7 }, layer: { id: 'ref:x__fill' } },
      { id: 7, properties: { _wfid: 'wf1-a', OBJECTID: 7 }, layer: { id: 'ref:x__outline' } },
      { properties: { _wfid: 'wf1-b' }, layer: { id: 'ref:x__fill' } }, // 瓦片另一份/世界副本
      { properties: { id: 'plain-9' }, layer: { id: 'ref:x__point' } },
    ];
    expect(candidateIdsFromHits(hits as never)).toEqual([
      { id: 'wf1-a', sourceLayer: 'ref:x__fill' },
      { id: 'wf1-b', sourceLayer: 'ref:x__fill' },
      { id: 'plain-9', sourceLayer: 'ref:x__point' },
    ]);
  });

  it('截断到 MAX_PICK_CANDIDATES；无稳定 id 不出', () => {
    const hits = Array.from({ length: MAX_PICK_CANDIDATES + 4 }, (_, i) => ({
      properties: { OBJECTID: i }, layer: { id: 'ref:x__point' },
    }));
    const out = candidateIdsFromHits(hits as never);
    expect(out).toHaveLength(MAX_PICK_CANDIDATES);
    expect(candidateIdsFromHits([{ properties: { name: 'no-id' } }])).toEqual([]);
  });
});

describe('fetchAuthoritativeFeature', () => {
  it('链内命中：单请求返回完整 Feature', async () => {
    apiFetchMock.mockResolvedValueOnce({ type: 'Feature', geometry: { type: 'Point', coordinates: [1, 2] }, properties: {} });
    const f = await fetchAuthoritativeFeature({
      sessionId: 's', refId: 'ref:x', featureId: 'wf1-a', revision: 3,
    });
    expect(f?.geometry?.type).toBe('Point');
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(apiFetchMock.mock.calls[0][0]).toContain('/layers/data/ref%3Ax/feature/wf1-a');
    expect(apiFetchMock.mock.calls[0][0]).toContain('session_id=s');
  });

  it('404 + 数字 id → positional 重试带 v guard', async () => {
    const err = Object.assign(new Error('nf'), { status: 404 });
    apiFetchMock.mockRejectedValueOnce(err);
    apiFetchMock.mockResolvedValueOnce({ type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: {} });
    const f = await fetchAuthoritativeFeature({
      sessionId: 's', refId: 'ref:x', featureId: '42', revision: 9,
    });
    expect(f).not.toBeNull();
    const secondUrl = apiFetchMock.mock.calls[1][0] as string;
    expect(secondUrl).toContain('id_mode=positional');
    expect(secondUrl).toContain('v=9');
  });

  it('非数字 id 404 → null（不重试）；positional 仍 404 → null', async () => {
    apiFetchMock.mockRejectedValueOnce(Object.assign(new Error('nf'), { status: 404 }));
    expect(await fetchAuthoritativeFeature({ sessionId: 's', refId: 'ref:x', featureId: 'wf1-zz' })).toBeNull();
    expect(apiFetchMock).toHaveBeenCalledTimes(1);

    apiFetchMock.mockReset();
    apiFetchMock.mockRejectedValue(Object.assign(new Error('nf'), { status: 404 }));
    expect(await fetchAuthoritativeFeature({ sessionId: 's', refId: 'ref:x', featureId: '42', revision: 4 })).toBeNull();
    expect(apiFetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('pickAuthoritativeFeature', () => {
  function fakeMap(hits: unknown[], projectImpl?: (p: [number, number]) => { x: number; y: number }) {
    return {
      queryRenderedFeatures: vi.fn(() => hits),
      project: projectImpl ?? ((p: [number, number]) => ({ x: p[0] * 100, y: -p[1] * 100 })),
      getStyle: () => ({
        layers: [
          { id: 'ref:x__fill' }, { id: 'ref:x__outline' }, { id: 'other__point' },
        ],
      }),
    } as unknown as MaplibreMap;
  }

  it('A03：命中只认服务端完整几何 —— 候选几何（瓦片简化）不参与判定', async () => {
    // 渲染面候选：长线要素（瓦片里可能被抽稀成两端点）
    const hits = [{ properties: { _wfid: 'wf1-line' }, layer: { id: 'ref:x__line' } }];
    // 服务端权威：中段远离点击点的完整几何 → 不命中
    apiFetchMock.mockResolvedValueOnce({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [[104.0, 30.0], [104.2, 30.0]] },
      properties: {},
    });
    const r = await pickAuthoritativeFeature({
      map: fakeMap(hits),
      point: { x: 10450, y: -3050 }, // (104.5, 30.5) —— 离权威几何很远
      lngLat: [104.5, 30.5],
      layerId: 'ref:x', refId: 'ref:x', tolerancePx: 6,
    });
    expect(r).toBeNull();
  });

  it('命中返回权威几何与 id；多候选取命中距离最近', async () => {
    const hits = [
      { properties: { _wfid: 'wf1-far' }, layer: { id: 'ref:x__fill' } },
      { properties: { _wfid: 'wf1-near' }, layer: { id: 'ref:x__fill' } },
    ];
    apiFetchMock.mockImplementation(async (_path: string) => {
      const url = _path as string;
      if (url.includes('wf1-near')) {
        return { type: 'Feature', geometry: { type: 'Point', coordinates: [104.5, 30.5] }, properties: { k: 1 } };
      }
      return { type: 'Feature', geometry: { type: 'Point', coordinates: [104.5, 30.53] }, properties: { k: 2 } };
    });
    const r = await pickAuthoritativeFeature({
      map: fakeMap(hits),
      point: { x: 10450, y: -3050 },
      lngLat: [104.5, 30.5],
      layerId: 'ref:x', refId: 'ref:x', tolerancePx: 6,
    });
    expect(r?.id).toBe('wf1-near');
    expect(r?.distPx).toBe(0);
  });

  it('A17：signal 已取消 → 不发回查请求', async () => {
    const hits = [{ properties: { _wfid: 'wf1-a' }, layer: { id: 'ref:x__fill' } }];
    const controller = new AbortController();
    controller.abort();
    const r = await pickAuthoritativeFeature({
      map: fakeMap(hits),
      point: { x: 1, y: 1 },
      lngLat: [104.5, 30.5],
      layerId: 'ref:x', refId: 'ref:x', signal: controller.signal,
    });
    expect(r).toBeNull();
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it('queryRenderedFeatures 只查目标图层的子层（其它图层命中不混入）', async () => {
    const map = fakeMap([]);
    const qrf = map.queryRenderedFeatures as unknown as ReturnType<typeof vi.fn>;
    await pickAuthoritativeFeature({
      map, point: { x: 1, y: 1 }, lngLat: [0, 0], layerId: 'ref:x', refId: 'ref:x',
    });
    const arg = qrf.mock.calls[0][1] as { layers: string[] };
    expect(arg.layers).toEqual(['ref:x__fill', 'ref:x__outline']);
  });
});
