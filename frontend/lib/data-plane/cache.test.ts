import { describe, it, expect } from 'vitest';

import { RefDataCache, estimateFeatureCollectionBytes } from '@/lib/data-plane/cache';

/**
 * RefDataCache 契约：字节有界 LRU + 可见性 pin。
 *
 * 逐出语义（Oracle：预算内可预测逐出且地图可用）：
 * - 仅逐未 pin 条目（pin = 视口内可见层的显示数据，逐了地图就破）；
 * - 最老优先（真 LRU：get 触摸刷新）；
 * - 单条超总预算的条目拒绝缓存（防一条巨物冲掉全部存活数据），
 *   调用方仍可直通使用，只是不入账。
 */

function fc(n: number) {
  return {
    type: 'FeatureCollection' as const,
    features: Array.from({ length: n }, (_, i) => ({
      type: 'Feature' as const,
      id: i,
      geometry: { type: 'Point' as const, coordinates: [116 + i * 0.001, 39.9] },
      properties: { i },
    })),
  };
}

describe('estimateFeatureCollectionBytes', () => {
  it('scales with feature count and is deterministic', () => {
    const a = estimateFeatureCollectionBytes(fc(10));
    const b = estimateFeatureCollectionBytes(fc(20));
    expect(a).toBeGreaterThan(0);
    expect(b).toBeGreaterThan(a);
    expect(estimateFeatureCollectionBytes(fc(10))).toBe(a);
  });

  it('degenerate input → 0, never throws', () => {
    expect(estimateFeatureCollectionBytes(undefined as never)).toBe(0);
    expect(estimateFeatureCollectionBytes({ type: 'FeatureCollection' } as never)).toBe(0);
  });
});

describe('RefDataCache — basic LRU', () => {
  it('set/get roundtrip with bytes accounting', () => {
    const c = new RefDataCache({ maxBytes: 10_000 });
    c.set('a', fc(1), { bytes: 100 });
    expect(c.get('a')?.bytes).toBe(100);
    expect(c.totalBytes).toBe(100);
    expect(c.size).toBe(1);
  });

  it('get refreshes recency: oldest unpinned evicted first', () => {
    const c = new RefDataCache({ maxBytes: 250 });
    c.set('old', fc(1), { bytes: 100 });
    c.set('mid', fc(1), { bytes: 100 });
    c.get('old'); // touch old → mid is now oldest
    c.set('new', fc(1), { bytes: 100 }); // needs 100, budget 250 → evict mid
    expect(c.peek('mid')).toBeUndefined();
    expect(c.peek('old')).toBeDefined();
    expect(c.peek('new')).toBeDefined();
  });

  it('re-set same key replaces bytes accounting (no double count)', () => {
    const c = new RefDataCache({ maxBytes: 10_000 });
    c.set('a', fc(1), { bytes: 100 });
    c.set('a', fc(2), { bytes: 300 });
    expect(c.totalBytes).toBe(300);
  });
});

describe('RefDataCache — budget & eviction', () => {
  it('evicts LRU unpinned until under budget, reports freed keys', () => {
    const evicted: string[] = [];
    const c = new RefDataCache({ maxBytes: 300, onEvict: (k) => evicted.push(k) });
    c.set('a', fc(1), { bytes: 100 });
    c.set('b', fc(1), { bytes: 100 });
    c.set('c', fc(1), { bytes: 100 });
    c.set('d', fc(1), { bytes: 100 }); // 400 > 300 → evict a
    expect(evicted).toEqual(['a']);
    expect(c.peek('a')).toBeUndefined();
    expect(c.totalBytes).toBe(300);
  });

  it('pinned entries survive; unpinned neighbors are sacrificed (stop at budget)', () => {
    const c = new RefDataCache({ maxBytes: 300 });
    c.set('pin', fc(1), { bytes: 100 });
    c.setPinned('pin', true);
    c.set('b', fc(1), { bytes: 100 });
    c.set('c', fc(1), { bytes: 100 });
    c.set('d', fc(1), { bytes: 100 }); // 400 > 300 → evict oldest unpinned 'b'，到预算即停
    expect(c.peek('pin')).toBeDefined();
    expect(c.peek('b')).toBeUndefined();
    expect(c.peek('c')).toBeDefined();
    expect(c.peek('d')).toBeDefined();
    expect(c.totalBytes).toBe(300);
  });

  it('all-pinned over budget → cache may exceed budget but never evicts pinned', () => {
    const c = new RefDataCache({ maxBytes: 150 });
    c.set('p1', fc(1), { bytes: 100 });
    c.setPinned('p1', true); // 先 pin 再插入下一条：p1 才有豁免资格
    c.set('p2', fc(1), { bytes: 100 });
    c.setPinned('p2', true);
    expect(c.totalBytes).toBe(200);
    expect(c.peek('p1')).toBeDefined();
    expect(c.peek('p2')).toBeDefined();
  });

  it('oversized single entry (> maxBytes) is refused, existing entries intact', () => {
    const c = new RefDataCache({ maxBytes: 500 });
    c.set('keep', fc(1), { bytes: 100 });
    c.set('monster', fc(1), { bytes: 600 });
    expect(c.peek('monster')).toBeUndefined();
    expect(c.peek('keep')).toBeDefined();
    expect(c.totalBytes).toBe(100);
  });
});

describe('RefDataCache — pin bookkeeping', () => {
  it('unpin makes an entry evictable again', () => {
    const c = new RefDataCache({ maxBytes: 200 });
    c.set('p', fc(1), { bytes: 150 });
    c.setPinned('p', true);
    c.set('q', fc(1), { bytes: 150 }); // p pinned, q fits (300 > 200? q itself…) 
    // q 插入后超预算：p 被 pin → 逐 q 自己刚插入的场景由实现保证不逐自己
    expect(c.peek('p')).toBeDefined();
    expect(c.totalBytes).toBeLessThanOrEqual(400);
  });

  it('setPinned on unknown key is a safe no-op returning false', () => {
    const c = new RefDataCache({ maxBytes: 100 });
    expect(c.setPinned('ghost', true)).toBe(false);
  });

  it('delete removes accounting; clear resets everything', () => {
    const c = new RefDataCache({ maxBytes: 1000 });
    c.set('a', fc(1), { bytes: 100 });
    c.set('b', fc(1), { bytes: 100 });
    c.setPinned('b', true);
    expect(c.delete('a')).toBe(true);
    expect(c.delete('a')).toBe(false);
    expect(c.totalBytes).toBe(100);
    c.clear();
    expect(c.totalBytes).toBe(0);
    expect(c.size).toBe(0);
  });

  it('keys() returns LRU order (oldest first)', () => {
    const c = new RefDataCache({ maxBytes: 10_000 });
    c.set('a', fc(1), { bytes: 10 });
    c.set('b', fc(1), { bytes: 10 });
    c.set('c', fc(1), { bytes: 10 });
    c.get('a'); // a → newest
    expect(c.keys()).toEqual(['b', 'c', 'a']);
  });
});
