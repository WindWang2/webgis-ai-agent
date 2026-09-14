// 方向 8（ADR-0183 / U4）：每笔用户 mutation 携带客户端幂等键 —— 弱网重试
// 由服务端幂等去重收敛；队列观测（深度/峰值/样本环）只读不扰语义。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  getPendingPresentation,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import {
  commitExplicitView,
  getMutationQueueStats,
  newMutationId,
  toggleLayerAndCommit,
} from '@/lib/mapspec/user-mutation';
import { getPendingMutationMeta } from '@/lib/mapspec/session-cursor';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const fetchMock = vi.fn();

function okBody(payload: Record<string, unknown>) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: () => Promise.resolve(JSON.stringify(payload)),
  };
}

function committedResponse(revision: number, visible: boolean) {
  return okBody({
    success: true,
    origin: 'user',
    mutation_revision: revision,
    mapspec: {
      version: '1.0',
      sources: { L1: { type: 'geojson' } },
      layers: [
        {
          id: 'L1',
          source: 'L1',
          type: 'circle',
          layout: { visibility: visible ? 'visible' : 'none' },
        },
      ],
    },
  });
}

beforeEach(async () => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset();
  // 排干串行链：上一个测试若遗留排队操作，让它在被重置的 mock 上快速
  // 失败并释放链条。
  await new Promise((resolve) => setTimeout(resolve, 0));
  useHudStore.getState().clearLayers();
  setMapSpecSessionCursor('sid-mtid', 3);
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

describe('client_mutation_id propagation', () => {
  it('sends client_mutation_id and stamps the same id onto pending meta', async () => {
    let sentBody: any = null;
    let release!: (value: unknown) => void;
    fetchMock.mockImplementationOnce((_url: string, init: any) => {
      sentBody = JSON.parse(init.body);
      return new Promise((resolve) => {
        release = resolve;
      });
    });

    const done = toggleLayerAndCommit('L1');
    // 在途：pending 挂着，meta 与服务端幂等键同票。
    await vi.waitFor(() => expect(sentBody).not.toBeNull());
    const id = sentBody?.client_mutation_id;
    expect(typeof id).toBe('string');
    expect(id.length).toBeGreaterThanOrEqual(8);
    expect(getPendingMutationMeta('L1')?.mutationId).toBe(id);

    release(committedResponse(4, false));
    await done;
    // 提交成功后 pending 清空，meta 一并清除。
    expect(getPendingPresentation()).toEqual({});
    expect(getPendingMutationMeta('L1')).toBeNull();
  });

  it('carries client_mutation_id on set_view commits', async () => {
    let sentBody: any = null;
    fetchMock.mockImplementationOnce((_url: string, init: any) => {
      sentBody = JSON.parse(init.body);
      return Promise.resolve(
        okBody({
          success: true,
          mutation_revision: 4,
          mapspec: { version: '1.0', sources: {}, layers: [] },
        }),
      );
    });

    await commitExplicitView({ center: [116, 40], zoom: 10 });
    expect(sentBody?.intent).toBe('set_view');
    expect(typeof sentBody?.client_mutation_id).toBe('string');
  });

  it('mints fresh, distinct ids per operation', () => {
    const a = newMutationId();
    const b = newMutationId();
    expect(a).not.toBe(b);
    expect(a).toMatch(/^[0-9a-f-]{36}$/);
  });
});

describe('duplicate (idempotent replay) responses', () => {
  it('converges like success: revision + spec applied, no rollback', async () => {
    // 模拟：首笔已提交但响应丢失（游标停 3）；重试同 id → 服务端返回
    // duplicate + 存证 revision 4 + 当前权威 spec。
    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(
        okBody({
          success: true,
          duplicate: true,
          mutation_revision: 4,
          mutation_id: 'dup-1',
          mapspec: {
            version: '1.0',
            sources: { L1: { type: 'geojson' } },
            layers: [
              {
                id: 'L1',
                source: 'L1',
                type: 'circle',
                layout: { visibility: 'none' },
              },
            ],
          },
        }),
      ),
    );

    await toggleLayerAndCommit('L1');

    // 游标推进到存证 revision；committed spec 采纳；无残留 pending。
    expect(getMapSpecSessionCursor().revision).toBe(4);
    expect(getCommittedMapSpec()?.layers?.[0].layout).toEqual({ visibility: 'none' });
    expect(getPendingPresentation()).toEqual({});
  });
});

describe('mutation queue observability', () => {
  it('tracks depth peak and bounded settle samples across chained ops', async () => {
    const release: Array<(v: unknown) => void> = [];
    fetchMock.mockImplementation(() => new Promise((resolve) => {
      release.push(resolve);
    }));

    const first = toggleLayerAndCommit('L1');
    await vi.waitFor(() => expect(release.length).toBe(1));
    const second = toggleLayerAndCommit('L1'); // 排队等待首笔落定
    // 串行链深度峰值 ≥ 2（一笔在飞 + 一笔排队）。
    expect(getMutationQueueStats().depthPeak).toBeGreaterThanOrEqual(2);

    release[0](committedResponse(4, false));
    await first;
    await vi.waitFor(() => expect(release.length).toBe(2));
    release[1](committedResponse(5, false));
    await second;

    const stats = getMutationQueueStats();
    expect(stats.depth).toBe(0);
    // 样本环跨测试共享（模块级、有界 32）—— 只断言落笔后的范围界。
    expect(stats.samples.length).toBeGreaterThanOrEqual(2);
    expect(stats.samples.length).toBeLessThanOrEqual(32);
    for (const sample of stats.samples) {
      expect(sample.settleMs).toBeGreaterThanOrEqual(0);
    }
    expect(getPendingPresentation()).toEqual({});
  });
});
