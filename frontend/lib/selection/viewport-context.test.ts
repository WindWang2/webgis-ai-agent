/**
 * ViewportContext（§12）契约：debounce / 指纹去重 / epoch stale 取消。
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import {
  flushViewportContext,
  getViewportContext,
  publishViewportContext,
  resetViewportContext,
} from './viewport-context';

describe('viewport context', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetViewportContext();
  });

  it('publishes after debounce', () => {
    publishViewportContext([104, 30, 104.1, 30.1], 10);
    expect(getViewportContext()).toBeNull();
    vi.advanceTimersByTime(350);
    const ctx = getViewportContext();
    expect(ctx?.bbox).toEqual([104, 30, 104.1, 30.1]);
    expect(ctx?.zoom).toBe(10);
    expect(ctx?.generation).toBeGreaterThan(0);
  });

  it('suppresses duplicate fingerprints', () => {
    publishViewportContext([104, 30, 104.1, 30.1], 10);
    vi.advanceTimersByTime(350);
    const gen1 = getViewportContext()?.generation;
    publishViewportContext([104, 30, 104.1, 30.1], 10); // same quantized fp
    vi.advanceTimersByTime(350);
    expect(getViewportContext()?.generation).toBe(gen1); // no re-emit
  });

  it('quantizes near-identical bboxes (anti-jitter)', () => {
    publishViewportContext([104.000001, 30, 104.1, 30.1], 10);
    vi.advanceTimersByTime(350);
    const gen1 = getViewportContext()?.generation;
    publishViewportContext([104.000002, 30, 104.1, 30.1], 10);
    vi.advanceTimersByTime(350);
    expect(getViewportContext()?.generation).toBe(gen1);
  });

  it('new extent republishes (generation advances)', () => {
    publishViewportContext([104, 30, 104.1, 30.1], 10);
    vi.advanceTimersByTime(350);
    const gen1 = getViewportContext()?.generation;
    publishViewportContext([105, 31, 105.1, 31.1], 11);
    vi.advanceTimersByTime(350);
    expect(getViewportContext()?.generation).toBeGreaterThan(gen1!);
  });

  it('session reset cancels pending debounced publication', () => {
    publishViewportContext([104, 30, 104.1, 30.1], 10);
    resetViewportContext(); // flush pending timer + clear
    vi.advanceTimersByTime(1000);
    expect(getViewportContext()).toBeNull();
  });

  it('invalid payloads are rejected', () => {
    expect(publishViewportContext([NaN, 30, 104, 31], 10, 0)).toBe(false);
    expect(publishViewportContext([104, 30, 104.1, 30.1], Number.NaN, 0)).toBe(false);
  });

  it('flush cancels without publishing', () => {
    publishViewportContext([104, 30, 104.1, 30.1], 10);
    flushViewportContext();
    vi.advanceTimersByTime(1000);
    expect(getViewportContext()).toBeNull();
  });
});
