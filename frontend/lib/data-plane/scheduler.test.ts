import { describe, it, expect, vi } from 'vitest';

import { DataPlaneScheduler } from '@/lib/data-plane/scheduler';
import type { RefFetchDeps, RefFetchRequest } from '@/lib/data-plane/scheduler';

/**
 * DataPlaneScheduler 契约（extreme-scale v2）：
 * - 并发上限 + 优先级出队（同优先级 FIFO，无饥饿：队列必然排空）；
 * - 单飞去重（同 session+ref 并发请求共享一次 fetch）；
 * - 缓存命中不触发网络；ETag 条件请求 → 304 命中缓存；
 * - 取消：会话/单 ref 两条路径；取消后迟到的 fetch 完成绝不落账
 *   （onFulfilled 不调用，stale-write 防护是调度器的硬保证）；
 * - 失败不缓存、不重试（重试语义归 transport）。
 */

function fc(n = 1) {
  return {
    type: 'FeatureCollection' as const,
    features: Array.from({ length: n }, (_, i) => ({
      type: 'Feature' as const,
      id: i,
      geometry: { type: 'Point' as const, coordinates: [116, 39] },
      properties: {},
    })),
  };
}

interface Harness {
  scheduler: DataPlaneScheduler;
  inflight: Set<string>;
  resolveAll: () => void;
  fetchCalls: Array<{ refId: string; etag?: string; signal: AbortSignal }>;
}

function makeHarness(opts?: {
  concurrency?: number;
  impl?: RefFetchDeps['fetchImpl'];
}): Harness {
  const inflight = new Set<string>();
  const fetchCalls: Harness['fetchCalls'] = [];
  let resolvers: Array<() => void> = [];
  const scheduler = new DataPlaneScheduler({
    concurrency: opts?.concurrency ?? 2,
    fetchImpl: opts?.impl ?? ((req, signal) => {
      fetchCalls.push({ refId: req.refId, etag: req.etag, signal });
      inflight.add(req.refId);
      return new Promise((resolve) => {
        resolvers.push(() => {
          inflight.delete(req.refId);
          resolve({ fc: fc(2), etag: `W/"${req.refId}-1"` });
        });
      });
    }),
  });
  return {
    scheduler,
    inflight,
    fetchCalls,
    resolveAll: () => {
      const r = resolvers;
      resolvers = [];
      r.forEach((fn) => fn());
    },
  };
}

function req(over: Partial<RefFetchRequest> = {}): RefFetchRequest {
  return { sessionId: 's1', refId: 'ref:a', ...over };
}

describe('DataPlaneScheduler — basic fulfillment', () => {
  it('resolves with fc + etag and writes cache; second request hits cache without network', async () => {
    const h = makeHarness();
    const first = h.scheduler.request(req());
    h.resolveAll();
    const r1 = await first;
    expect(r1.status).toBe('fulfilled');
    expect(r1.fc?.features).toHaveLength(2);
    expect(r1.etag).toBe('W/"ref:a-1"');

    const r2 = await h.scheduler.request(req());
    expect(r2.status).toBe('fulfilled');
    expect(r2.fromCache).toBe(true);
    expect(h.scheduler.stats().fetchOk).toBe(1);
    expect(h.scheduler.stats().cacheHits).toBe(1);
  });

  it('onFulfilled fires exactly once with (refId, fc, etag)', async () => {
    const onFulfilled = vi.fn();
    const h = makeHarness();
    h.scheduler.setOnFulfilled(onFulfilled);
    const p = h.scheduler.request(req());
    h.resolveAll();
    await p;
    expect(onFulfilled).toHaveBeenCalledTimes(1);
    expect(onFulfilled).toHaveBeenCalledWith('ref:a', expect.anything(), 'W/"ref:a-1"');
  });
});

describe('DataPlaneScheduler — single flight & concurrency', () => {
  it('concurrent duplicate requests share ONE fetch (dedup)', async () => {
    const h = makeHarness();
    const p1 = h.scheduler.request(req());
    const p2 = h.scheduler.request(req());
    h.resolveAll();
    const [r1, r2] = await Promise.all([p1, p2]);
    expect(r1.fc).toBe(r2.fc);
    expect(h.scheduler.stats().deduped).toBe(1);
    expect(h.fetchCalls).toHaveLength(1);
  });

  it('respects concurrency cap: third request waits for a slot', async () => {
    const h = makeHarness({ concurrency: 2 });
    h.scheduler.request(req({ refId: 'ref:1' }));
    h.scheduler.request(req({ refId: 'ref:2' }));
    const p3 = h.scheduler.request(req({ refId: 'ref:3' }));
    await Promise.resolve();
    expect(h.inflight.size).toBe(2);
    h.resolveAll(); // frees 1&2, 3 starts
    await Promise.resolve();
    await Promise.resolve();
    expect(h.inflight.size).toBe(1);
    expect(h.inflight.has('ref:3')).toBe(true);
    h.resolveAll();
    await p3;
    expect(h.fetchCalls).toHaveLength(3);
  });

  it('higher priority dequeues first (slot occupied by a gate request)', async () => {
    const order: string[] = [];
    let release!: () => void;
    const gate = new Promise<{ fc: ReturnType<typeof fc>; etag: string }>((r) => {
      release = () => r({ fc: fc(1), etag: 'gate' });
    });
    const scheduler = new DataPlaneScheduler({
      concurrency: 1,
      fetchImpl: (r) => {
        if (r.refId === 'ref:gate') return gate;
        order.push(r.refId);
        return Promise.resolve({ fc: fc(1), etag: 'e' });
      },
    });
    scheduler.request(req({ refId: 'ref:gate' })); // 占住唯一并发槽
    scheduler.request(req({ refId: 'ref:low', priority: 0 }));
    scheduler.request(req({ refId: 'ref:high', priority: 999 }));
    release(); // 槽释放 → 队列按优先级出队
    await scheduler.whenIdle();
    expect(order).toEqual(['ref:high', 'ref:low']);
  });

  it('setPriority bumps a queued request', async () => {
    const order: string[] = [];
    let release!: () => void;
    const gate = new Promise<{ fc: ReturnType<typeof fc>; etag: string }>((r) => {
      release = () => r({ fc: fc(1), etag: 'gate' });
    });
    const scheduler = new DataPlaneScheduler({
      concurrency: 1,
      fetchImpl: (r) => {
        if (r.refId === 'ref:gate') return gate;
        order.push(r.refId);
        return Promise.resolve({ fc: fc(1) });
      },
    });
    scheduler.request(req({ refId: 'ref:gate' }));
    scheduler.request(req({ refId: 'ref:a', priority: 10 }));
    scheduler.request(req({ refId: 'ref:b', priority: 5 }));
    scheduler.setPriority('ref:b', 's1', 50);
    release();
    await scheduler.whenIdle();
    expect(order).toEqual(['ref:b', 'ref:a']);
  });
});

describe('DataPlaneScheduler — ETag / 304', () => {
  it('revalidates with If-None-Match etag; 304 resolves from cache', async () => {
    const impl = vi.fn(async (r: RefFetchRequest) =>
      r.etag ? 'not-modified' as const : { fc: fc(3), etag: 'W/"v1"' },
    );
    const h = makeHarness({ impl });
    const r1 = await h.scheduler.request(req());
    expect(r1.status).toBe('fulfilled');
    const r2 = await h.scheduler.request(req({ revalidate: true }));
    expect(r2.status).toBe('not-modified');
    expect(r2.fromCache).toBe(true);
    expect(r2.fc?.features).toHaveLength(3); // cache 的旧数据继续服务
    expect(impl).toHaveBeenLastCalledWith(expect.objectContaining({ etag: 'W/"v1"' }), expect.anything());
    expect(h.scheduler.stats().etag304).toBe(1);
  });

  it('200 after revalidation refreshes cache and reports fulfilled', async () => {
    let n = 0;
    const impl = vi.fn(async () => ({ fc: fc(++n), etag: `W/"v${n}"` }));
    const h = makeHarness({ impl });
    await h.scheduler.request(req());
    const r2 = await h.scheduler.request(req({ revalidate: true }));
    expect(r2.status).toBe('fulfilled');
    expect(r2.etag).toBe('W/"v2"');
    expect(h.scheduler.cachePeek('s1::ref:a')?.fc.features).toHaveLength(2);
  });

  it('no cached entry → etag never sent even with revalidate', async () => {
    const impl = vi.fn(async () => ({ fc: fc(1), etag: 'W/"v1"' }));
    const h = makeHarness({ impl });
    await h.scheduler.request(req({ revalidate: true }));
    expect(h.fetchCalls[0]?.etag).toBeUndefined();
  });

  it('fresh-TTL entry serves from cache without conditional round trip', async () => {
    const impl = vi.fn(async () => ({ fc: fc(2), etag: 'W/"v1"' }));
    const h = makeHarness({ impl });
    const r1 = await h.scheduler.request(req());
    expect(r1.status).toBe('fulfilled');
    const r2 = await h.scheduler.request(req()); // TTL 内 → 直接缓存服务
    expect(r2.fromCache).toBe(true);
    expect(impl).toHaveBeenCalledTimes(1);
  });
});

describe('DataPlaneScheduler — cancellation & stale protection', () => {
  it('cancelSession drops queued + aborts in-flight, both reported cancelled', async () => {
    const h = makeHarness({ concurrency: 1 });
    const p1 = h.scheduler.request(req({ refId: 'ref:1' })); // 在飞
    const p2 = h.scheduler.request(req({ refId: 'ref:2' })); // 排队
    const cancelled = h.scheduler.cancelSession('s1');
    expect(cancelled).toBe(2);
    const r2 = await p2; // 排队条目立即结算
    expect(r2.status).toBe('cancelled');
    h.resolveAll(); // ref:1 迟到完成
    const r1 = await p1;
    expect(r1.status).toBe('cancelled');
    expect(h.scheduler.stats().cancelled).toBe(2);
  });

  it('cancelled in-flight request NEVER calls onFulfilled (stale-write hard guard)', async () => {
    const onFulfilled = vi.fn();
    const h = makeHarness({ concurrency: 1 });
    h.scheduler.setOnFulfilled(onFulfilled);
    const p = h.scheduler.request(req({ refId: 'ref:stale' }));
    h.scheduler.cancelSession('s1');
    h.resolveAll();
    await p;
    expect(onFulfilled).not.toHaveBeenCalled();
  });

  it('fetch rejects after cancel → result cancelled (not failed), no throw', async () => {
    const scheduler = new DataPlaneScheduler({
      concurrency: 1,
      fetchImpl: (_r, signal) => new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      }),
    });
    const p = scheduler.request(req());
    scheduler.cancelSession('s1');
    const r = await p;
    expect(r.status).toBe('cancelled');
    expect(scheduler.stats().fetchFailed).toBe(0);
  });

  it('cancelRef cancels just that ref', async () => {
    const h = makeHarness({ concurrency: 1 });
    const p1 = h.scheduler.request(req({ refId: 'ref:x' }));
    const p2 = h.scheduler.request(req({ refId: 'ref:y' }));
    expect(h.scheduler.cancelRef('ref:x', 's1')).toBe(1);
    h.resolveAll(); // x 迟到完成（结算为 cancelled）→ 槽释放，y 起飞
    expect(await p1).toMatchObject({ status: 'cancelled' });
    h.resolveAll(); // y 完成
    expect(await p2).toMatchObject({ status: 'fulfilled' });
  });

  it('session-scoped: cancelSession(other) leaves s1 untouched', async () => {
    const h = makeHarness({ concurrency: 1 });
    const p = h.scheduler.request(req({ sessionId: 's2', refId: 'ref:s2' }));
    h.scheduler.cancelSession('s1');
    h.resolveAll();
    expect((await p).status).toBe('fulfilled');
  });
});

describe('DataPlaneScheduler — failure semantics', () => {
  it('fetch failure → status failed with error; nothing cached; retry allowed', async () => {
    let attempts = 0;
    const scheduler = new DataPlaneScheduler({
      concurrency: 1,
      fetchImpl: async () => {
        attempts += 1;
        if (attempts === 1) throw new Error('boom');
        return { fc: fc(1), etag: 'W/"ok"' };
      },
    });
    const r1 = await scheduler.request(req());
    expect(r1.status).toBe('failed');
    expect((r1.error as Error).message).toBe('boom');
    expect(scheduler.cachePeek('s1::ref:a')).toBeUndefined();
    const r2 = await scheduler.request(req());
    expect(r2.status).toBe('fulfilled');
    expect(attempts).toBe(2);
  });
});

describe('DataPlaneScheduler — pinning (visibility-aware cache)', () => {
  it('pin keeps entry through eviction pressure; unpin releases it', async () => {
    const h = makeHarness();
    await (async () => {
      const p = h.scheduler.request(req({ refId: 'ref:pin' }));
      h.resolveAll();
      await p;
    })();
    h.scheduler.pinRef('s1::ref:pin', true);
    expect(h.scheduler.cachePeek('s1::ref:pin')).toBeDefined();
    h.scheduler.pinRef('s1::ref:pin', false);
    expect(h.scheduler.cachePeek('s1::ref:pin')).toBeDefined(); // 仍在：未到预算
  });
});
