/**
 * G25 实例登记簿 + 快照 LRU store 测试（A09/A10/A11/A14 契约）。
 */
import { describe, expect, it, beforeEach } from 'vitest';

import {
  DEFAULT_LIVE_MAP_BUDGET,
  mapInstanceRegistry,
} from '@/components/map/frames/frame-instance-registry';
import { FrameSnapshotStore, type FrameSnapshotEntry } from '@/lib/cartography/frame-snapshot-store';

const snap = (frameId: string, fingerprint: string, bytes = 100, over: Partial<FrameSnapshotEntry> = {}): FrameSnapshotEntry => ({
  sessionId: 's-1',
  frameId,
  fingerprint,
  dataUrl: `data:image/png;base64,${'x'.repeat(bytes)}`,
  bytes,
  specRevision: 3,
  dataRevision: 5,
  viewportSize: { width: 400, height: 300 },
  capturedAt: 1,
  camera: null,
  ...over,
});

describe('mapInstanceRegistry（A09 预算仲裁）', () => {
  beforeEach(() => mapInstanceRegistry.resetForTest());

  it('register/release 计数；主图计入预算', () => {
    expect(mapInstanceRegistry.liveCount()).toBe(0);
    const main = mapInstanceRegistry.register('main', 'main');
    expect(mapInstanceRegistry.liveCount()).toBe(1);
    const f1 = mapInstanceRegistry.register('frame', 'frame:f1');
    expect(mapInstanceRegistry.liveCount()).toBe(2);
    expect(mapInstanceRegistry.liveCountsByKind()).toEqual({ main: 1, frame: 1, comparison: 0 });
    f1.release();
    expect(mapInstanceRegistry.liveCount()).toBe(1);
    main.release();
    expect(mapInstanceRegistry.liveCount()).toBe(0);
    expect(DEFAULT_LIVE_MAP_BUDGET).toBe(2);
  });

  it('重复注册同 key：旧代际失效，迟到 release 不误删新注册（A16）', () => {
    const gen1 = mapInstanceRegistry.register('frame', 'frame:f1');
    const gen2 = mapInstanceRegistry.register('frame', 'frame:f1'); // context 重建
    expect(gen2.generation).toBeGreaterThan(gen1.generation);
    expect(mapInstanceRegistry.liveCount()).toBe(1);
    gen1.release(); // 迟到
    expect(mapInstanceRegistry.liveCount()).toBe(1);
    gen2.release();
    expect(mapInstanceRegistry.liveCount()).toBe(0);
  });

  it('subscribe 通知计数变化；release 幂等', () => {
    const events: number[] = [];
    const unsub = mapInstanceRegistry.subscribe(() => events.push(mapInstanceRegistry.liveCount()));
    const h = mapInstanceRegistry.register('main', 'main');
    h.release();
    h.release();
    expect(events).toEqual([1, 0]);
    unsub();
  });
});

describe('FrameSnapshotStore（A10/A11/A14）', () => {
  it('put/get 指纹命中；revision 变化 → miss', () => {
    const store = new FrameSnapshotStore();
    expect(store.put(snap('f1', 'fp-1'))).toBe('stored');
    expect(store.get('f1', 'fp-1')?.frameId).toBe('f1');
    expect(store.get('f1', 'fp-2')).toBeNull(); // 指纹变化 = 新版本
    const st = store.stats();
    expect(st.hits).toBe(1);
    expect(st.misses).toBe(1);
  });

  it('项数上限 LRU 逐出；get touch 改变逐出序', () => {
    const store = new FrameSnapshotStore(3);
    store.put(snap('a', 'fa'));
    store.put(snap('b', 'fb'));
    store.put(snap('c', 'fc'));
    store.get('a', 'fa'); // touch a → b 变最旧
    store.put(snap('d', 'fd')); // 逐出 b
    expect(store.peek('b')).toBeNull();
    expect(store.peek('a')?.frameId).toBe('a');
    expect(store.stats().count).toBe(3);
  });

  it('字节上限逐出；单条超限拒绝不挤他人', () => {
    const store = new FrameSnapshotStore(10, 250);
    store.put(snap('a', 'fa', 100));
    store.put(snap('b', 'fb', 100));
    expect(store.put(snap('big', 'fx', 10_000))).toBe('rejected_oversize');
    expect(store.put(snap('c', 'fc', 100))).toBe('stored'); // 总 300 > 250 → 逐出 a
    expect(store.peek('a')).toBeNull();
    expect(store.peek('b')?.frameId).toBe('b');
    expect(store.stats().bytes).toBeLessThanOrEqual(250);
  });

  it('invalidateFrame / invalidateSession 清除无残留（A14）', () => {
    const store = new FrameSnapshotStore();
    store.put(snap('f1', 'fp-1'));
    store.put(snap('f2', 'fp-2', 100, { sessionId: 's-2' }));
    store.invalidateFrame('f1');
    expect(store.peek('f1')).toBeNull();
    store.invalidateSession('s-2');
    expect(store.peek('f2')).toBeNull();
    expect(store.stats().count).toBe(0);
    expect(store.stats().bytes).toBe(0);
  });
});
