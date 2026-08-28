import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  commitMapSpecDocument,
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  getPendingPresentation,
  getPendingRemoved,
  mergePendingPresentation,
  setMapSpecRevision,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import {
  commitExplicitView,
  removeLayerAndCommit,
  setLayerOpacityAndCommit,
  toggleLayerAndCommit,
} from '@/lib/mapspec/user-mutation';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const fetchMock = vi.fn();

beforeEach(async () => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset();
  // 排干串行链：上一个测试若遗留排队操作，让它在被重置的 mock 上快速
  // 失败并释放链条（否则后续测试全部排在永不结算的 promise 之后）。
  await new Promise((resolve) => setTimeout(resolve, 0));
  useHudStore.getState().clearLayers();
  setMapSpecSessionCursor('sid-1', 3);
  useHudStore.getState().addLayer({
    id: 'L1',
    name: 'Schools',
    type: 'vector',
    visible: true,
    opacity: 1,
    group: 'analysis',
    source: { type: 'FeatureCollection', features: [] } as any,
    _mapspecLayerId: 'L1',
  } as any);
});

describe('toggleLayerAndCommit', () => {
  it('holds a pending overlay until the mutation ACK commits MapSpec', async () => {
    let release!: (value: unknown) => void;
    fetchMock.mockImplementationOnce(() => new Promise((resolve) => {
      release = resolve;
    }));

    const done = toggleLayerAndCommit('L1');
    expect(getPendingPresentation()).toEqual({ L1: { visible: false } });
    expect(useHudStore.getState().layers[0].visible).toBe(false);
    // 串行链在微任务上启动操作——先让链开跑（fetch 被调用、release 赋值）。
    await Promise.resolve();

    release({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: () => Promise.resolve(JSON.stringify({
        success: true,
        origin: 'user',
        mutation_revision: 4,
        mapspec: {
          version: '1.0',
          sources: { L1: { type: 'geojson' } },
          layers: [{ id: 'L1', source: 'L1', type: 'circle', layout: { visibility: 'none' } }],
        },
      })),
    });
    await done;

    expect(getPendingPresentation()).toEqual({});
    expect(getCommittedMapSpec()?.layers?.[0].layout).toEqual({ visibility: 'none' });
  });

  it('applies pending HUD visibility then hydrates from committed MapSpec', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: () => Promise.resolve(JSON.stringify({
        success: true,
        origin: 'user',
        mutation_revision: 4,
        mapspec: {
          layers: [{ id: 'L1', layout: { visibility: 'none' } }],
        },
      })),
    });

    await toggleLayerAndCommit('L1');

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/chat/sessions/sid-1/mapspec/mutations'),
      expect.objectContaining({ method: 'POST' }),
    );
    expect(useHudStore.getState().layers[0].visible).toBe(false);
  });

  it('rolls pending visibility back when the mutation is rejected', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: () => Promise.resolve(JSON.stringify({ detail: { status: 'superseded' } })),
    });

    await toggleLayerAndCommit('L1');

    expect(useHudStore.getState().layers[0].visible).toBe(true);
  });
});

describe('setLayerOpacityAndCommit vs Agent upsert race', () => {
  it('drops pending opacity and hydrates the Agent MapSpec so a retry uses the new revision', async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        statusText: 'Conflict',
        text: () => Promise.resolve(JSON.stringify({
          detail: {
            status: 'superseded',
            mutation_revision: 5,
            correction_hint: 'Re-read MapSpec and retry with the current mutation_revision.',
            mapspec: {
              layers: [{
                id: 'L1',
                layout: { visibility: 'visible' },
                paint: { opacity: 1, 'circle-opacity': 1, 'circle-color': '#ff0000' },
              }],
            },
          },
        })),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: () => Promise.resolve(JSON.stringify({
          success: true,
          mutation_revision: 6,
          mapspec: {
            layers: [{
              id: 'L1',
              layout: { visibility: 'visible' },
              paint: { opacity: 0.4, 'circle-opacity': 0.4, 'circle-color': '#ff0000' },
            }],
          },
        })),
      });

    await setLayerOpacityAndCommit('L1', 0.4);

    expect(useHudStore.getState().layers[0].opacity).toBe(1);
    expect(getMapSpecSessionCursor().revision).toBe(5);

    await setLayerOpacityAndCommit('L1', 0.4);

    const second = JSON.parse(fetchMock.mock.calls[1][1].body as string);
    expect(second.expected_revision).toBe(5);
    expect(useHudStore.getState().layers[0].opacity).toBe(0.4);
    expect(getMapSpecSessionCursor().revision).toBe(6);
  });
});

describe('commitExplicitView', () => {
  it('posts set_view and stores the framed MapSpec', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: () => Promise.resolve(JSON.stringify({
        success: true,
        mutation_revision: 4,
        mapspec: { view: { center: [114, 30], zoom: 10, framed: true } },
      })),
    });

    await commitExplicitView({ center: [114, 30], zoom: 10 });

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.intent).toBe('set_view');
    expect(body.center).toEqual([114, 30]);
    expect(body.expected_revision).toBe(3);
    expect(getMapSpecSessionCursor().revision).toBe(4);
  });
});

describe('removeLayerAndCommit', () => {
  it('restores HUD layers when the mutation is rejected without a MapSpec', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Error',
      headers: { get: () => null },
      text: () => Promise.resolve(JSON.stringify({ detail: 'boom' })),
    });

    await removeLayerAndCommit('L1');
    expect(useHudStore.getState().layers).toHaveLength(1);
    expect(useHudStore.getState().layers[0].id).toBe('L1');
  });

  it('ST-P1-1: retries once with the server revision after 409 superseded, then clears pendingRemoved', async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        statusText: 'Conflict',
        text: () => Promise.resolve(JSON.stringify({
          detail: {
            status: 'superseded',
            mutation_revision: 7,
            mapspec: { layers: [{ id: 'L1', layout: { visibility: 'visible' } }] },
          },
        })),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: () => Promise.resolve(JSON.stringify({
          success: true,
          mutation_revision: 8,
          mapspec: { layers: [] },
        })),
      });

    await removeLayerAndCommit('L1');

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const second = JSON.parse(fetchMock.mock.calls[1][1].body as string);
    expect(second.intent).toBe('remove_layer');
    // 重试必须携带 superseded 回传的新 revision，否则必然再 409。
    expect(second.expected_revision).toBe(7);
    expect(getPendingRemoved()).toEqual([]);
    expect(getMapSpecSessionCursor().revision).toBe(8);
    expect(useHudStore.getState().layers).toHaveLength(0);
  });

  it('ST-P1-1: double superseded keeps pendingRemoved so compose suppresses the zombie layer', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: () => Promise.resolve(JSON.stringify({
        detail: {
          status: 'superseded',
          mutation_revision: 9,
          mapspec: { layers: [{ id: 'L1', layout: { visibility: 'visible' } }] },
        },
      })),
    });

    await removeLayerAndCommit('L1');

    expect(fetchMock).toHaveBeenCalledTimes(2);
    // 服务端仍含被删层：pendingRemoved 必须保留，否则 compose 把 L1 复活。
    expect(getPendingRemoved()).toEqual(['L1']);
    expect(useHudStore.getState().layers).toHaveLength(0);
  });
});

describe('user mutation serialization (ST-P1-2)', () => {
  it('concurrent toggles on different layers post sequentially with distinct revisions (no 409 storm)', async () => {
    useHudStore.getState().clearLayers();
    useHudStore.getState().addLayer({
      id: 'A', name: 'A', type: 'vector', visible: true, opacity: 1,
      group: 'analysis', source: { type: 'FeatureCollection', features: [] } as any,
      _mapspecLayerId: 'A',
    } as any);
    useHudStore.getState().addLayer({
      id: 'B', name: 'B', type: 'vector', visible: true, opacity: 1,
      group: 'analysis', source: { type: 'FeatureCollection', features: [] } as any,
      _mapspecLayerId: 'B',
    } as any);

    let rev = 3;
    fetchMock.mockImplementation(async () => {
      rev += 1;
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        text: () => Promise.resolve(JSON.stringify({
          success: true,
          mutation_revision: rev,
          mapspec: { layers: [{ id: 'A', layout: { visibility: 'none' } }] },
        })),
      };
    });

    await Promise.all([toggleLayerAndCommit('A'), toggleLayerAndCommit('B')]);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const bodies = fetchMock.mock.calls.map((c) => JSON.parse(c[1].body as string));
    // 串行链：第二笔必须读到第一笔推进后的 revision（4），而非相同的 3。
    expect(bodies[0].expected_revision).toBe(3);
    expect(bodies[1].expected_revision).toBe(4);
  });

  it('applyCommittedMapSpec does not overwrite an in-flight optimistic toggle on another layer', async () => {
    useHudStore.getState().clearLayers();
    useHudStore.getState().addLayer({
      id: 'A', name: 'A', type: 'vector', visible: true, opacity: 1,
      group: 'analysis', source: { type: 'FeatureCollection', features: [] } as any,
      _mapspecLayerId: 'A',
    } as any);
    useHudStore.getState().addLayer({
      id: 'B', name: 'B', type: 'vector', visible: true, opacity: 1,
      group: 'analysis', source: { type: 'FeatureCollection', features: [] } as any,
      _mapspecLayerId: 'B',
    } as any);

    // 层 B 有在途乐观展示（HUD 已翻开 + pending 未落），层 A 的响应携带
    // B 的旧真相（visibility none）——回灌不得把 B 的在途乐观态打回去。
    mergePendingPresentation('B', { visible: true });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: () => Promise.resolve(JSON.stringify({
        success: true,
        mutation_revision: 4,
        mapspec: {
          layers: [
            { id: 'A', layout: { visibility: 'none' } },
            { id: 'B', layout: { visibility: 'none' } },
          ],
        },
      })),
    });

    await setLayerOpacityAndCommit('A', 0.5);

    // A 由本次响应回灌（none），B 的在途乐观值不被旧真相覆盖。
    expect(useHudStore.getState().layers.find((l) => l.id === 'A')?.visible).toBe(false);
    expect(useHudStore.getState().layers.find((l) => l.id === 'B')?.visible).toBe(true);
    // B 的 pending 仍在（尚未提交）。
    expect(getPendingPresentation().B).toEqual({ visible: true });
  });
});

describe('session cursor revision monotonicity (ST-P3-1)', () => {
  it('a stale SSE event cannot regress the cursor revision or commit an older spec', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: () => Promise.resolve(JSON.stringify({
        success: true,
        mutation_revision: 5,
        mapspec: { layers: [{ id: 'L1', layout: { visibility: 'none' } }] },
      })),
    });

    await commitExplicitView({ center: [114, 30], zoom: 10 });
    expect(getMapSpecSessionCursor().revision).toBe(5);
    const fresh = getCommittedMapSpec();
    expect(fresh?.layers?.[0]?.id).toBe('L1');

    // 迟到的旧代次事件：revision 回退被拒；旧 spec 不覆盖 committed。
    setMapSpecRevision(4);
    commitMapSpecDocument({ layers: [{ id: 'STALE', layout: { visibility: 'none' } }] }, 4);

    expect(getMapSpecSessionCursor().revision).toBe(5);
    expect(getCommittedMapSpec()?.layers?.[0]?.id).toBe('L1');

    // 同代次（== 当前）允许提交：正常路径不受影响。
    commitMapSpecDocument({ layers: [{ id: 'SAME-REV', layout: {} }] }, 5);
    expect(getCommittedMapSpec()?.layers?.[0]?.id).toBe('SAME-REV');
  });
});
