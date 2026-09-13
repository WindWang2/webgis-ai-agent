// 方向 8（ADR-0183 / U4）：pending per-op 身份（ST-P3-3 收口）。
// 身份记在旁路 meta 表 —— pending 本体形状不变，compose 零感知。
import { beforeEach, describe, expect, it } from 'vitest';
import {
  clearPendingPresentation,
  getPendingMutationMeta,
  getPendingPresentation,
  mergePendingPresentation,
  resetLiveState,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';

beforeEach(() => {
  setMapSpecSessionCursor('sid-mtid', 1);
  resetLiveState();
});

describe('pending mutation meta (per-op identity)', () => {
  it('stamps a fresh monotonic generation per optimistic patch', () => {
    mergePendingPresentation('L1', { visible: false }, 'mtid-a');
    const first = getPendingMutationMeta('L1');
    expect(first?.gen).toBe(1);
    expect(first?.mutationId).toBe('mtid-a');

    mergePendingPresentation('L1', { opacity: 0.5 }, 'mtid-b');
    const second = getPendingMutationMeta('L1');
    expect(second?.gen).toBe(2);
    expect(second?.mutationId).toBe('mtid-b');
  });

  it('keeps the previous mutationId when an alias merge carries no id', () => {
    mergePendingPresentation('L1', { visible: false }, 'mtid-a');
    const before = getPendingMutationMeta('L1')?.gen ?? 0;
    // 别名层（同笔操作的第二 id）不带票 → 继承，不换票。
    mergePendingPresentation('L1-alias', { visible: false });
    const alias = getPendingMutationMeta('L1-alias');
    expect(alias?.mutationId).toBeUndefined();
    // 同 id 无票合并同样继承旧票。
    mergePendingPresentation('L1', { opacity: 0.25 });
    expect(getPendingMutationMeta('L1')?.mutationId).toBe('mtid-a');
    // 但代数仍单调推进（模块级计数器跨测试不重置 —— 只断言相对单调）。
    expect(getPendingMutationMeta('L1')?.gen).toBeGreaterThan(before);
  });

  it('clears meta with the pending entry (targeted and bulk)', () => {
    mergePendingPresentation('L1', { visible: false }, 'mtid-a');
    mergePendingPresentation('L2', { opacity: 0.1 }, 'mtid-b');
    clearPendingPresentation('L1');
    expect(getPendingMutationMeta('L1')).toBeNull();
    expect(getPendingMutationMeta('L2')?.mutationId).toBe('mtid-b');
    clearPendingPresentation();
    expect(getPendingMutationMeta('L2')).toBeNull();
  });

  it('leaves the pending value shape untouched for compose consumers', () => {
    mergePendingPresentation('L1', { visible: false }, 'mtid-a');
    expect(getPendingPresentation()).toEqual({ L1: { visible: false } });
  });

  it('returns null for layers without in-flight patches', () => {
    expect(getPendingMutationMeta('nope')).toBeNull();
  });
});
