/**
 * Interaction IR review 清偿（前端侧，ADR-0224 / round-1 review P1-3/P1-5）：
 * mutation 链接事件的诚实性 ——
 * - reorder 成功 → layer_order_changed committed（真实 mutation id）；
 * - mutation 从未发生（会话切换 enqueue 短路）→ **不 emit**（P1-3 假 committed）；
 * - 失败/superseded 也只在仍处原会话时 emit（P1-5 跨会话投毒）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import { getMapSpecSessionCursor, setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import {
  reorderLayersAndCommit,
  setLayerOpacityAndCommit,
} from '@/lib/mapspec/user-mutation';
import { emitMutationLinked } from '@/lib/interaction/bus';

vi.mock('@/lib/interaction/bus', () => ({
  emitMutationLinked: vi.fn(),
}));

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const fetchMock = vi.fn();

const okMutationResponse = (revision: number) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  text: () => Promise.resolve(JSON.stringify({
    success: true,
    origin: 'user',
    mutation_revision: revision,
    mapspec: {
      version: '1.0',
      sources: { L1: { type: 'geojson' } },
      layers: [{ id: 'L1', source: 'L1', type: 'circle', layout: { visibility: 'visible' } }],
    },
  })),
});

beforeEach(async () => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset();
  await new Promise((resolve) => setTimeout(resolve, 0));
  useHudStore.getState().clearLayers();
  setMapSpecSessionCursor('sid-ir', 3);
  for (const layer of [
    { id: 'L1', name: 'Roads', visible: true, opacity: 1, _mapspecLayerId: 'L1' },
    { id: 'L2', name: 'Parks', visible: true, opacity: 1, _mapspecLayerId: 'L2' },
  ]) {
    useHudStore.getState().addLayer({
      id: layer.id, name: layer.name, type: 'vector', visible: layer.visible,
      opacity: layer.opacity, group: 'analysis',
      source: { type: 'FeatureCollection', features: [] } as any,
      _mapspecLayerId: layer._mapspecLayerId,
    } as any);
  }
});

describe('reorderLayersAndCommit · Interaction IR 事件', () => {
  it('committed → emit layer_order_changed（带真实 client_mutation_id 与 revision）', async () => {
    fetchMock.mockResolvedValueOnce(okMutationResponse(7));
    const layers = useHudStore.getState().layers.slice().reverse();
    await reorderLayersAndCommit(layers);
    expect(emitMutationLinked).toHaveBeenCalledTimes(1);
    const [kind, payload, refs] = vi.mocked(emitMutationLinked).mock.calls[0];
    expect(kind).toBe('layer_order_changed');
    // 位移摘要：被移动层的 id + from/to 索引（HUD 行序受分组排序影响，
    // 断言语义而非具体方向）。
    expect(['L1', 'L2']).toContain((payload as { layer_id: string }).layer_id);
    expect(payload).toMatchObject({ from_index: 1, to_index: 0 });
    expect(refs?.clientMutationId).toMatch(/^[A-Za-z0-9-]{8,}$/);
    expect(refs?.outcome).toBe('committed');
    expect(refs?.mutationRevision).toBe(7);
    // 服务端收到的 mutation 与事件共享同一 id（causality 链）。
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.client_mutation_id).toBe(refs?.clientMutationId);
  });

  it('会话切换（mutation 从未发生）→ 不 emit（P1-3：不得申报 committed）', async () => {
    // mutation POST 成功返回，但返回时游标已切到另一会话 →
    // commitMapSpecMutation 的会话守卫短路 return undefined。
    fetchMock.mockImplementationOnce(async () => {
      setMapSpecSessionCursor('sid-other', 3);
      return okMutationResponse(7);
    });
    const layers = useHudStore.getState().layers.slice().reverse();
    await reorderLayersAndCommit(layers);
    expect(emitMutationLinked).not.toHaveBeenCalled();
  });
});

describe('setLayerOpacityAndCommit · Interaction IR 事件', () => {
  it('失败（网络错）→ emit failed', async () => {
    fetchMock.mockRejectedValueOnce(new Error('network down'));
    await setLayerOpacityAndCommit('L1', 0.4).catch(() => undefined);
    expect(emitMutationLinked).toHaveBeenCalledTimes(1);
    const [kind, , refs] = vi.mocked(emitMutationLinked).mock.calls[0];
    expect(kind).toBe('layer_opacity_changed');
    expect(refs?.outcome).toBe('failed');
  });

  it('失败且已切会话 → 不 emit（P1-5：旧会话尝试不得进新会话 ledger）', async () => {
    fetchMock.mockImplementationOnce(async () => {
      setMapSpecSessionCursor('sid-other', 3);
      throw new Error('network down');
    });
    await setLayerOpacityAndCommit('L1', 0.4).catch(() => undefined);
    expect(emitMutationLinked).not.toHaveBeenCalled();
    expect(getMapSpecSessionCursor().sessionId).toBe('sid-other');
  });

  it('committed → emit committed（原会话）', async () => {
    fetchMock.mockResolvedValueOnce(okMutationResponse(9));
    await setLayerOpacityAndCommit('L1', 0.4);
    expect(emitMutationLinked).toHaveBeenCalledTimes(1);
    const [kind, payload, refs] = vi.mocked(emitMutationLinked).mock.calls[0];
    expect(kind).toBe('layer_opacity_changed');
    expect(payload).toEqual({ layer_id: 'L1', opacity: 0.4 });
    expect(refs?.outcome).toBe('committed');
    expect(refs?.mutationRevision).toBe(9);
  });
});
