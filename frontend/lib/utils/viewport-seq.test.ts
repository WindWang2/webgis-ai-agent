import { describe, it, expect } from 'vitest';
import {
  createViewportSeqTracker,
  nextViewportSeq,
  coalesceViewportState,
} from './viewport-seq';

describe('nextViewportSeq', () => {
  it('bumps monotonically from the current value', () => {
    const t = createViewportSeqTracker();
    expect(nextViewportSeq(t)).toBe(1);
    expect(nextViewportSeq(t)).toBe(2);
    expect(t.current).toBe(2);
  });

  it('starts from the current value when a tracker already holds one', () => {
    const t = createViewportSeqTracker(7);
    expect(nextViewportSeq(t)).toBe(8);
  });
});

describe('coalesceViewportState', () => {
  it('applies incoming state when its seq is strictly newer than the client', () => {
    const t = createViewportSeqTracker();
    nextViewportSeq(t); // client sent seq 1
    const out = coalesceViewportState(t, 2, { center: [1, 2], zoom: 10 });
    expect(out.viewport).toEqual({ center: [1, 2], zoom: 10 });
    expect(out.seq).toBe(2);
    expect(t.current).toBe(2);
  });

  it('ignores stale state with an older seq (must NOT fly the map back)', () => {
    const t = createViewportSeqTracker();
    nextViewportSeq(t); // client sent seq 1
    nextViewportSeq(t); // client sent seq 2
    const out = coalesceViewportState(t, 1, { center: [9, 9], zoom: 5 });
    expect(out.viewport).toBeNull();
    expect(out.seq).toBe(2);
    expect(t.current).toBe(2);
  });

  it('ignores state with an equal seq (already known)', () => {
    const t = createViewportSeqTracker();
    nextViewportSeq(t);
    expect(coalesceViewportState(t, 1, { center: [1, 2], zoom: 10 }).viewport).toBeNull();
  });

  it('applies state with no viewport payload as a no-op', () => {
    const t = createViewportSeqTracker();
    const out = coalesceViewportState(t, 5, undefined);
    expect(out.viewport).toBeNull();
    expect(t.current).toBe(0);
  });

  it('applies state with no seq info (server has no sequencing yet)', () => {
    const t = createViewportSeqTracker();
    const out = coalesceViewportState(t, undefined, { center: [1, 2], zoom: 10 });
    expect(out.viewport).toEqual({ center: [1, 2], zoom: 10 });
    expect(t.current).toBe(0); // nothing learned, nothing skipped
  });

  it('out-of-order arrival across a restore resolves to the latest seq', () => {
    const t = createViewportSeqTracker();
    // client pans during a turn: sends seq 1, 2 (throttled POSTs)
    nextViewportSeq(t);
    nextViewportSeq(t);
    // session restore returns the server's older seq 1 — must be dropped
    expect(coalesceViewportState(t, 1, { center: [9, 9], zoom: 5 }).viewport).toBeNull();
    // a later server write (e.g. ws_service) at seq 3 — newer, applied
    const out = coalesceViewportState(t, 3, { center: [4, 5], zoom: 8 });
    expect(out.viewport).toEqual({ center: [4, 5], zoom: 8 });
    expect(t.current).toBe(3);
  });
});
