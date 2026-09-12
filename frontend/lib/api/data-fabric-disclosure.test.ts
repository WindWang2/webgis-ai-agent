import { describe, expect, it, beforeEach } from 'vitest';
import {
  recordFabricDisclosure,
  getDisclosureSnapshot,
  subscribeFabricDisclosure,
  resetFabricDisclosureForTests,
} from './data-fabric-disclosure';
import {
  breakerClosedFixture,
  breakerOpenFixture,
  breakerHalfOpenFixture,
  cacheLocalHitFixture,
  cacheDistributedHitFixture,
} from '@/test/fixtures/health-fixtures';

describe('data-fabric 披露留存（ADR-0142 D5）', () => {
  beforeEach(() => resetFabricDisclosureForTests());

  it('从联邦查询载荷中抽取 engine_breaker 键', () => {
    const recorded = recordFabricDisclosure({ engine: 'v5_fallback', engine_breaker: breakerOpenFixture, rows: [] });
    expect(recorded).toBe(true);
    expect(getDisclosureSnapshot().breaker?.state).toBe('open');
    expect(getDisclosureSnapshot().breaker?.consecutive_failures).toBe(3);
    expect(getDisclosureSnapshot().breaker?.failure_threshold).toBe(3);
  });

  it('抽取 result_cache 键（分布式 basis）', () => {
    recordFabricDisclosure({ result_cache: cacheDistributedHitFixture });
    const snap = getDisclosureSnapshot();
    expect(snap.cache?.hit).toBe(true);
    expect(snap.cache?.basis).toBe('ttl+fingerprint+distributed');
    expect(snap.cache?.age_s).toBeNull();
  });

  it('三态留存：closed → open → half_open 取最近一次', () => {
    recordFabricDisclosure({ engine_breaker: breakerClosedFixture });
    recordFabricDisclosure({ engine_breaker: breakerOpenFixture });
    recordFabricDisclosure({ engine_breaker: breakerHalfOpenFixture });
    expect(getDisclosureSnapshot().breaker?.state).toBe('half_open');
    expect(getDisclosureSnapshot().recorded).toBe(3);
  });

  it('非披露载荷（缺键/坏形状）不产生留存', () => {
    expect(recordFabricDisclosure({ rows: [] })).toBe(false);
    expect(recordFabricDisclosure({ engine_breaker: { state: 'BROKEN' } })).toBe(false);
    expect(recordFabricDisclosure(null)).toBe(false);
    expect(getDisclosureSnapshot().recorded).toBe(0);
    expect(getDisclosureSnapshot().breaker).toBeNull();
  });

  it('订阅者在披露到达时收到快照，退订后不再收到', () => {
    const seen: number[] = [];
    const unsub = subscribeFabricDisclosure((snap) => seen.push(snap.recorded));
    recordFabricDisclosure({ engine_breaker: breakerClosedFixture });
    recordFabricDisclosure({ result_cache: cacheLocalHitFixture });
    unsub();
    recordFabricDisclosure({ engine_breaker: breakerOpenFixture });
    expect(seen).toEqual([1, 2]);
  });

  it('LRU 有界：历史超过 32 条截断', () => {
    for (let i = 0; i < 40; i += 1) {
      recordFabricDisclosure({ engine_breaker: { ...breakerClosedFixture, consecutive_failures: i } });
    }
    expect(getDisclosureSnapshot().recorded).toBe(32);
    // 最近一次在前
    expect(getDisclosureSnapshot().breaker?.consecutive_failures).toBe(39);
  });
});
