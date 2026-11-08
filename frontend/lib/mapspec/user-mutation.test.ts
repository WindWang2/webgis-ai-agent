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
  refreshMapSpecAfterExternalMutation,
  removeLayerAndCommit,
  reorderLayersAndCommit,
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

describe('V7: HUD-only rows (no _mapspecLayerId) bypass the server mutation channel', () => {
  it('toggle on an unbound row is local-only (no POST, no pending, no toast path)', async () => {
    useHudStore.getState().clearLayers();
    useHudStore.getState().addLayer({
      id: 'wb-sketch', name: '草图图层', type: 'vector', visible: true, opacity: 1,
      group: 'analysis', source: { type: 'FeatureCollection', features: [] } as any,
      // 无 _mapspecLayerId —— 服务端未知层
    } as any);

    await toggleLayerAndCommit('wb-sketch');

    expect(fetchMock).not.toHaveBeenCalled();
    expect(useHudStore.getState().layers[0].visible).toBe(false);
    expect(getPendingPresentation()).toEqual({});
  });

  it('opacity on an unbound row is local-only', async () => {
    useHudStore.getState().clearLayers();
    useHudStore.getState().addLayer({
      id: 'wb-sketch', name: '草图图层', type: 'vector', visible: true, opacity: 1,
      group: 'analysis', source: { type: 'FeatureCollection', features: [] } as any,
    } as any);

    await setLayerOpacityAndCommit('wb-sketch', 0.4);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(useHudStore.getState().layers[0].opacity).toBe(0.4);
  });

  it('remove of an unbound row deletes locally without POST and stays deleted', async () => {
    useHudStore.getState().clearLayers();
    useHudStore.getState().addLayer({
      id: 'wb-sketch', name: '草图图层', type: 'vector', visible: true, opacity: 1,
      group: 'analysis', source: { type: 'FeatureCollection', features: [] } as any,
    } as any);
    fetchMock.mockRejectedValue(new Error('must not be called'));

    await removeLayerAndCommit('wb-sketch');

    expect(fetchMock).not.toHaveBeenCalled();
    expect(useHudStore.getState().layers).toHaveLength(0);
  });
});

describe('V7: rollback session guards', () => {
  it('remove failure after a session switch does not resurrect the row into the new session', async () => {
    fetchMock.mockImplementation(() => new Promise((_resolve, reject) => {
      reject(new TypeError('network down'));
    }));

    const done = removeLayerAndCommit('L1');
    // 网络失败窗口内切会话：游标指向新会话。
    setMapSpecSessionCursor('sid-2', 1);
    await done;

    // 新会话表内不得出现旧会话的行（回滚被会话复核拦截）。
    const ids = useHudStore.getState().layers.map((l) => l.id);
    expect(ids).not.toContain('L1');
  });

  it('reorder failure after a session switch does not overwrite the new session order', async () => {
    useHudStore.getState().clearLayers();
    useHudStore.getState().addLayer({
      id: 'N1', name: 'N1', type: 'vector', visible: true, opacity: 1,
      group: 'analysis', source: { type: 'FeatureCollection', features: [] } as any,
      _mapspecLayerId: 'N1',
    } as any);
    fetchMock.mockImplementation(() => new Promise((_resolve, reject) => {
      reject(new TypeError('network down'));
    }));

    const done = reorderLayersAndCommit([{ id: 'N1', _mapspecLayerId: 'N1' }]);
    setMapSpecSessionCursor('sid-2', 1);
    await done;

    // 新会话的行仍在（旧会话顺序回滚被拦截）。
    expect(useHudStore.getState().layers.map((l) => l.id)).toEqual(['N1']);
  });
});

/* ─── C-1（review-fix-rereview-20261108）：REST 通道外突变的会话内刷新 ──────
 *
 * style-only 版本恢复（map-product-versions 面板）经 REST 直接突变权威
 * MapSpec：SSE/协作总线不会有任何事件。若前端不主动推进游标并回灌
 * committed spec/presentation，地图与图层面板停留在恢复前的样式，且用户
 * 下一次 presentation 提交携带过期 revision 必然 409 superseded。
 */
describe('refreshMapSpecAfterExternalMutation', () => {
  const mapStateResponse = (revision: number) => ({
    ok: true,
    status: 200,
    statusText: 'OK',
    text: () => Promise.resolve(JSON.stringify({
      map_state: {
        mapspec: {
          version: '1.0',
          sources: { L1: { type: 'geojson' } },
          layers: [{
            id: 'L1', source: 'L1', type: 'circle',
            layout: { visibility: 'none' }, paint: { opacity: 0.4 },
          }],
        },
        _cartographic_mutation_revision: revision,
      },
    })),
  });

  it('advances the cursor and applies the fetched committed presentation to HUD', async () => {
    fetchMock.mockImplementation((url: unknown) => (
      String(url).includes('/map-state') ? Promise.resolve(mapStateResponse(9)) : Promise.resolve({
        ok: true, status: 200, statusText: 'OK', text: () => Promise.resolve('{}'),
      })
    ));

    await refreshMapSpecAfterExternalMutation('sid-1', 9);

    expect(getMapSpecSessionCursor().revision).toBe(9);
    const l1 = useHudStore.getState().layers.find((l) => l.id === 'L1');
    expect(l1?.visible).toBe(false);
    expect(l1?.opacity).toBe(0.4);
    expect(getCommittedMapSpec()?.layers?.[0]).toMatchObject({ id: 'L1' });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/chat/sessions/sid-1/map-state'),
      expect.anything(),
    );
  });

  it('advances the revision from the mutation response even when map-state fetch fails', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('network down'));

    await expect(refreshMapSpecAfterExternalMutation('sid-1', 9)).resolves.toBeUndefined();

    // 游标已推进：下一次用户 mutation 携带新 revision，不会被 spurious 409。
    expect(getMapSpecSessionCursor().revision).toBe(9);
    // HUD 保持恢复前的本地真相（刷新失败的诚实保态，不伪造成功）。
    expect(useHudStore.getState().layers[0].visible).toBe(true);
  });

  it('never writes store state when the cursor moved to another session during the fetch', async () => {
    let release!: (value: unknown) => void;
    fetchMock.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));

    const done = refreshMapSpecAfterExternalMutation('sid-1', 9);
    setMapSpecSessionCursor('sid-2', 1);
    release(mapStateResponse(9));
    await done;

    expect(useHudStore.getState().layers[0].visible).toBe(true);
    expect(getMapSpecSessionCursor().sessionId).toBe('sid-2');
    expect(getMapSpecSessionCursor().revision).toBe(1);
  });

  it('falls back to the map-state revision when the mutation response omits one', async () => {
    fetchMock.mockImplementation((url: unknown) => (
      String(url).includes('/map-state') ? Promise.resolve(mapStateResponse(11)) : Promise.resolve({
        ok: true, status: 200, statusText: 'OK', text: () => Promise.resolve('{}'),
      })
    ));

    await refreshMapSpecAfterExternalMutation('sid-1');

    expect(getMapSpecSessionCursor().revision).toBe(11);
    expect(useHudStore.getState().layers[0].visible).toBe(false);
  });
});

/* ─── R2-N1/R2-N2（review-fix-rereview-20261108 Fix-2）：stale map-state 守卫 ── */
describe('refreshMapSpecAfterExternalMutation stale-state guard (R2)', () => {
  const staleState = (revision: number) => ({
    ok: true,
    status: 200,
    statusText: 'OK',
    text: () => Promise.resolve(JSON.stringify({
      map_state: {
        mapspec: {
          version: '1.0',
          sources: { LOLD: { type: 'geojson' } },
          layers: [{ id: 'LOLD', source: 'LOLD', type: 'circle', layout: { visibility: 'visible' } }],
        },
        _cartographic_mutation_revision: revision,
      },
    })),
  });

  it('R2-N1: does not mirror/prune HUD rows from a map-state that the cursor already superseded', async () => {
    // 预置：HUD 有一行绑定 spec 层 L9（fetch 期间的新 mutation 添加）。
    useHudStore.getState().clearLayers();
    useHudStore.getState().addLayer({
      id: 'row-9', name: 'L9', type: 'vector', visible: true, opacity: 1,
      group: 'analysis', source: { type: 'FeatureCollection', features: [] } as any,
      _mapspecLayerId: 'L9',
    } as any);

    let release!: (value: unknown) => void;
    fetchMock.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));

    const done = refreshMapSpecAfterExternalMutation('sid-1', 4);
    // fetch 在途时，一条更新的 mutation 已把游标推进到 20（SSE/串行链先落地）。
    setMapSpecRevision(20);
    release(staleState(4));
    await done;

    // stale spec（revision 4 < 20）不得成为 committed 文档，也不得以其层集
    // 镜像/修剪 HUD —— L9 行必须存活，LOLD 不得被镜像进来。
    expect(getMapSpecSessionCursor().revision).toBe(20);
    const ids = useHudStore.getState().layers.map((l) => l.id);
    expect(ids).toContain('row-9');
    expect(ids.some((id) => id === 'LOLD' || useHudStore.getState().layers.find((l) => l.id === id)?._mapspecLayerId === 'LOLD')).toBe(false);
    expect((getCommittedMapSpec() as { layers?: Array<{ id?: string }> } | null)?.layers?.[0]?.id).not.toBe('LOLD');
  });

  it('R2-N2: keeps the higher of the mutation-response revision and the fetched state revision', async () => {
    // restore 响应说 revision=5，但 fetch 到的会话真相已是 11（restore 与
    // fetch 之间又有 mutation 落地）—— 游标必须停在 11，否则下一笔用户
    // mutation 一次 spurious 409。
    fetchMock.mockImplementation((url: unknown) => (
      String(url).includes('/map-state') ? Promise.resolve(staleState(11)) : Promise.resolve({
        ok: true, status: 200, statusText: 'OK', text: () => Promise.resolve('{}'),
      })
    ));
    await refreshMapSpecAfterExternalMutation('sid-1', 5);
    expect(getMapSpecSessionCursor().revision).toBe(11);
  });
});
