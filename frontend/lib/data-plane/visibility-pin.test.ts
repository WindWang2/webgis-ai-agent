/**
 * F13 data plane 身份与 pin 契约测试（ADR-0214 D4）。
 *
 * 锁定：dataRevision 进缓存键（同 ref 异 revision 是不同条目/不同网络
 * 往返 —— #1112 同 ref 覆盖不串数据）；键解析对称；setRefPinned
 * revision 无关全代次 pin/unpin；可见层 pin 豁免预算逐出。
 */
import { describe, expect, it } from 'vitest';

import {
  DataPlaneScheduler,
  parseRefCacheKey,
  refCacheKey,
} from '@/lib/data-plane/scheduler';
import type { RefFetchRequest } from '@/lib/data-plane/scheduler';
import { RefDataCache } from '@/lib/data-plane/cache';
import {
  _resetVisibilityPinForTests,
  applyVisibilityPins,
  syncVisibilityPins,
  visibilityPinSignature,
} from '@/lib/data-plane/visibility-pin';

describe('refCacheKey', () => {
  it('backward compatible without revision', () => {
    expect(refCacheKey('s1', 'ref:a')).toBe('s1::ref:a');
    expect(refCacheKey('s1', 'ref:a', undefined)).toBe('s1::ref:a');
    expect(refCacheKey('s1', 'ref:a', '')).toBe('s1::ref:a');
  });

  it('revision-qualified key with revision present', () => {
    expect(refCacheKey('s1', 'ref:a', 3)).toBe('s1::ref:a@3');
    expect(refCacheKey('s1', 'ref:a', 'v2')).toBe('s1::ref:a@v2');
  });

  it('parse is symmetric (strips @rev suffix)', () => {
    expect(parseRefCacheKey('s1::ref:a')).toEqual({
      sessionId: 's1',
      refId: 'ref:a',
    });
    expect(parseRefCacheKey('s1::ref:a@3')).toEqual({
      sessionId: 's1',
      refId: 'ref:a',
    });
    // refId 本身含 @ 的极端形态：只剥最后一个 @ 段
    expect(parseRefCacheKey('s1::weird@ref@7').refId).toBe('weird@ref');
  });
});

describe('dataRevision cache identity', () => {
  function makeScheduler() {
    const fetches: string[] = [];
    const resolvers: Array<(v: { fc: unknown }) => void> = [];
    const scheduler = new DataPlaneScheduler({
      concurrency: 4,
      fetchImpl: (req: RefFetchRequest) => {
        fetches.push(refCacheKey(req.sessionId, req.refId, req.dataRevision));
        return new Promise((resolve) => {
          resolvers.push(() => resolve({ fc: { type: 'FeatureCollection', features: [] } }));
        });
      },
    });
    return {
      scheduler,
      fetches,
      flush: async () => {
        while (resolvers.length) resolvers.shift()!({ fc: {} });
        await scheduler.whenIdle();
      },
    };
  }

  it('same ref different revision = separate cache entries, separate fetches', async () => {
    const h = makeScheduler();
    const p1 = h.scheduler.request({ sessionId: 's', refId: 'r', dataRevision: 1 });
    const p2 = h.scheduler.request({ sessionId: 's', refId: 'r', dataRevision: 2 });
    await h.flush();
    await Promise.all([p1, p2]);
    // 两个 revision 各自一次网络往返（不去重 —— 数据可能已被覆盖）
    expect(h.fetches).toEqual(['s::r@1', 's::r@2']);
    // 两个缓存条目并存
    expect(h.scheduler.cachePeek('s::r@1')).toBeDefined();
    expect(h.scheduler.cachePeek('s::r@2')).toBeDefined();
  });

  it('same revision requests dedup into one round trip', async () => {
    const h = makeScheduler();
    const p1 = h.scheduler.request({ sessionId: 's', refId: 'r', dataRevision: 1 });
    const p2 = h.scheduler.request({ sessionId: 's', refId: 'r', dataRevision: 1 });
    const p3 = h.scheduler.request({ sessionId: 's', refId: 'r', dataRevision: 1 });
    await h.flush();
    await Promise.all([p1, p2, p3]);
    expect(h.fetches).toEqual(['s::r@1']);
  });

  it('revision-less requests keep legacy key (no cross-hits with revisioned)', async () => {
    const h = makeScheduler();
    const p1 = h.scheduler.request({ sessionId: 's', refId: 'r' });
    const p2 = h.scheduler.request({ sessionId: 's', refId: 'r', dataRevision: 1 });
    await h.flush();
    await Promise.all([p1, p2]);
    expect(h.fetches).toEqual(['s::r', 's::r@1']);
  });

  it('setPriority honours revision-qualified queue keys (queued, not in-flight)', async () => {
    // gate 模式（同 scheduler.test.ts 既有姿势）：并发 1 + 第一发 fetch
    // 永不落地 → 第二发留在**队列**里 —— setPriority 只扫队列。
    let releaseFirst!: () => void;
    const firstGate = new Promise<{ fc: unknown }>((resolve) => {
      releaseFirst = () => resolve({ fc: { type: 'FeatureCollection', features: [] } });
    });
    const scheduler = new DataPlaneScheduler({
      concurrency: 1,
      fetchImpl: () => firstGate,
    });
    void scheduler.request({ sessionId: 's', refId: 'gate', dataRevision: 0 }); // 占槽
    void scheduler.request({ sessionId: 's', refId: 'r', dataRevision: 2 });   // 排队
    expect(scheduler.setPriority('r', 's', 999, 2)).toBe(true);
    // 错 revision（或缺省）不命中
    expect(scheduler.setPriority('r', 's', 100, 1)).toBe(false);
    expect(scheduler.setPriority('r', 's', 100)).toBe(false);
    releaseFirst();
    await scheduler.whenIdle();
  });
});

describe('setRefPinned / unpinSession (visibility pin wiring)', () => {
  function cacheWith(keys: string[]): RefDataCache {
    const cache = new RefDataCache({ maxBytes: 10_000 });
    for (const key of keys) {
      cache.set(key, { type: 'FeatureCollection', features: [] });
    }
    return cache;
  }

  it('pins all revision generations of a ref (revision-agnostic)', () => {
    const cache = cacheWith(['s::r@1', 's::r@2', 's::other']);
    const scheduler = new DataPlaneScheduler({
      fetchImpl: () => Promise.resolve({ fc: { type: 'FeatureCollection', features: [] } }),
      cache,
    });
    expect(scheduler.setRefPinned('s', 'r', true)).toBe(2);
    expect(cache.peek('s::r@1')?.pinned).toBe(true);
    expect(cache.peek('s::r@2')?.pinned).toBe(true);
    expect(cache.peek('s::other')?.pinned).toBe(false);
    expect(scheduler.setRefPinned('s', 'r', false)).toBe(2);
    expect(cache.peek('s::r@1')?.pinned).toBe(false);
  });

  it('unpinSession sweeps all pinned entries of one session only', () => {
    const cache = cacheWith(['s1::a', 's1::b@3', 's2::a']);
    const scheduler = new DataPlaneScheduler({
      fetchImpl: () => Promise.resolve({ fc: { type: 'FeatureCollection', features: [] } }),
      cache,
    });
    scheduler.setRefPinned('s1', 'a', true);
    scheduler.setRefPinned('s1', 'b', true);
    scheduler.setRefPinned('s2', 'a', true);
    expect(cache.peek('s1::a')?.pinned).toBe(true);
    expect(cache.peek('s2::a')?.pinned).toBe(true);
    expect(scheduler.unpinSession('s1')).toBe(2);
    expect(cache.peek('s1::a')?.pinned).toBe(false);
    expect(cache.peek('s1::b@3')?.pinned).toBe(false);
    // 他会话不受影响
    expect(cache.peek('s2::a')?.pinned).toBe(true);
  });

  it('pinned visible data survives budget eviction that evicts unpinned', () => {
    // maxBytes 1000：每条 ~512+2*200=912 —— 三条必逐出
    const cache = new RefDataCache({ maxBytes: 1000 });
    const fc = { type: 'FeatureCollection', features: [{}, {}] };
    cache.set('s::hidden', fc);
    cache.set('s::visible@1', fc);
    cache.setPinned('s::visible@1', true);
    cache.set('s::third', fc); // 触发逐出
    // 未 pin 的最老条目先走；pin 条目豁免
    expect(cache.peek('s::visible@1')).toBeDefined();
  });
});

describe('visibility-pin module', () => {
  const layer = (id: string, refId: string | undefined, visible: boolean) =>
    ({ id, _refId: refId, visible }) as never as {
      _refId?: string;
      visible?: boolean;
    };

  it('signature is order-stable and visibility-sensitive', () => {
    const a = visibilityPinSignature(
      [layer('l1', 'ref:a', true), layer('l2', 'ref:b', false)], 's1');
    const b = visibilityPinSignature(
      [layer('l2', 'ref:b', false), layer('l1', 'ref:a', true)], 's1');
    expect(a).toBe(b);
    const c = visibilityPinSignature(
      [layer('l1', 'ref:a', false), layer('l2', 'ref:b', false)], 's1');
    expect(c).not.toBe(a);
  });

  it('applyVisibilityPins fail-opens without a configured scheduler', () => {
    // 无 configureDataPlane/无请求 → getRefScheduler 构造不抛；
    // 即使 session 缺失也绝不抛。
    const result = applyVisibilityPins(
      [{ id: 'l1', _refId: 'ref:a', visible: false } as never], 'sess-x');
    expect(result).toHaveProperty('pinned');
    expect(result).toHaveProperty('unpinned');
  });

  it('syncVisibilityPins is callable repeatedly (session tracking, no throw)', () => {
    _resetVisibilityPinForTests();
    expect(() => syncVisibilityPins()).not.toThrow();
    expect(() => syncVisibilityPins()).not.toThrow();
    _resetVisibilityPinForTests();
  });
});
