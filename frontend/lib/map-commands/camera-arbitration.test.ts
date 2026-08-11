import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  notifyUserGestureStart,
  notifyUserGestureEnd,
  isUserGesturing,
  onUserGestureStart,
  waitForGestureEnd,
  _resetCameraArbitrationForTests,
} from './camera-arbitration';

describe('camera-arbitration (V3 user-vs-AI camera ownership)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    _resetCameraArbitrationForTests();
  });

  afterEach(() => {
    vi.useRealTimers();
    _resetCameraArbitrationForTests();
  });

  it('tracks a single active gesture', () => {
    expect(isUserGesturing()).toBe(false);
    notifyUserGestureStart();
    expect(isUserGesturing()).toBe(true);
    notifyUserGestureEnd();
    expect(isUserGesturing()).toBe(false);
  });

  it('counts overlapping gesture starts (drag + zoom); ends only after all clear', () => {
    notifyUserGestureStart(); // drag
    notifyUserGestureStart(); // zoom
    notifyUserGestureEnd();   // drag end
    expect(isUserGesturing()).toBe(true); // zoom still active
    notifyUserGestureEnd();   // zoom end
    expect(isUserGesturing()).toBe(false);
  });

  it('waitForGestureEnd resolves immediately when no gesture is active', async () => {
    await expect(waitForGestureEnd()).resolves.toBeUndefined();
  });

  it('waitForGestureEnd resolves when the active gesture ends', async () => {
    notifyUserGestureStart();
    const wait = waitForGestureEnd();
    let settled = false;
    void wait.then(() => { settled = true; });

    // still pending while the gesture is active
    await vi.advanceTimersByTimeAsync(500);
    expect(settled).toBe(false);

    notifyUserGestureEnd();
    await vi.advanceTimersByTimeAsync(0);
    expect(settled).toBe(true);
  });

  it('waitForGestureEnd times out after the bound even if the gesture never ends', async () => {
    notifyUserGestureStart();
    const wait = waitForGestureEnd(3000);
    let settled = false;
    void wait.then(() => { settled = true; });

    await vi.advanceTimersByTimeAsync(2999);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(settled).toBe(true);
  });

  it('onUserGestureStart fires once per gesture-start edge and supports unsubscribe', () => {
    const cb = vi.fn();
    const off = onUserGestureStart(cb);

    notifyUserGestureStart();
    notifyUserGestureEnd();
    notifyUserGestureStart();
    // fired only when gesturing flipped false→true (edge), not per notification
    expect(cb).toHaveBeenCalledTimes(2);

    off();
    notifyUserGestureEnd();
    notifyUserGestureStart();
    expect(cb).toHaveBeenCalledTimes(2);
  });
});
