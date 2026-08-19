import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';
import { viewCommands } from './viewCommands';
import type { MapCommandContext } from './types';
import {
  notifyUserGestureStart,
  notifyUserGestureEnd,
  _resetCameraArbitrationForTests,
} from './camera-arbitration';

function makeCtx(map: any, params: Record<string, unknown>): MapCommandContext {
  return {
    map,
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => ({}),
    setSelectedBaseLayer: () => {},
    command: 'fly_to',
    params,
  } as MapCommandContext;
}

describe('viewCommands camera commands (V3 Promise<MapCommandResult> contract)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    _resetCameraArbitrationForTests();
  });

  afterEach(() => {
    vi.useRealTimers();
    _resetCameraArbitrationForTests();
  });

  it('fly_to starts the animation (stop() first, then flyTo())', () => {
    const map = makeMockMaplibreMap();
    void viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));

    expect(map._calls.flyTo).toHaveLength(1);
    expect(map._calls.flyTo[0]).toMatchObject({ center: [116, 39], zoom: 12, duration: 1500 });
    // self-interrupt: map.stop() ran before the new animation started
    expect(map.stop).toHaveBeenCalled();
    expect(map.stop.mock.invocationCallOrder[0]).toBeLessThan(
      map.flyTo.mock.invocationCallOrder[0],
    );
  });

  it('resolves succeeded on moveend with the SETTLED viewport as actual', async () => {
    const map = makeMockMaplibreMap();
    const promise = viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));

    // simulate the animation settling at a viewport that REACHED the requested
    // target (within the backend tolerance: center ≤0.001°, zoom ≤0.05)
    map._setViewport({ center: [116.0005, 39.0005], zoom: 12.02, bearing: 5, pitch: 10 });
    map._fire('moveend');
    // the settle check is deferred one frame (production event ordering)
    await vi.advanceTimersByTimeAsync(0);

    await expect(promise).resolves.toEqual({
      status: 'succeeded',
      result: { center: [116.0005, 39.0005], zoom: 12.02, bearing: 5, pitch: 10 },
    });
  });

  it('resolves failed superseded_by_user when a user gesture starts mid-flight', async () => {
    const map = makeMockMaplibreMap();
    const promise = viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));
    expect(map._calls.flyTo).toHaveLength(1);

    notifyUserGestureStart();

    await expect(promise).resolves.toEqual({ status: 'failed', error: 'superseded_by_user' });
    // The gesture listener settles superseded FIRST and never calls map.stop():
    // in real MapLibre stop() fires moveend synchronously (the once('moveend')
    // settle would win → wrong SUCCEEDED ack), and stop() would reset the user's
    // active gesture handlers. MapLibre stops the animation itself on gesture
    // start, so stop() is called exactly once — the self-interrupt at the start.
    expect(map._calls.stop).toHaveLength(1);
  });

  it('superseded when a user gesture is STILL active at the 3s wait bound (never starts the animation)', async () => {
    const map = makeMockMaplibreMap();
    // Gesture starts before the command; waitForGestureEnd gives up after 3s
    // while the gesture is STILL active (never ended).
    notifyUserGestureStart();

    const promise = viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));
    await vi.advanceTimersByTimeAsync(3000); // wait bound exceeded; gesture still active

    // ROUND-2: starting the animation (map.stop() + flyTo) would fight the
    // user's active handlers — settle superseded WITHOUT touching the camera.
    expect(map._calls.flyTo).toHaveLength(0);
    expect(map._calls.stop).toHaveLength(0);

    await expect(promise).resolves.toEqual({ status: 'failed', error: 'superseded_by_user' });
  });

  it('waits out an active user gesture that ENDS within the bound, then starts the animation', async () => {
    const map = makeMockMaplibreMap();
    notifyUserGestureStart();

    const promise = viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));
    // not started while the user owns the camera
    expect(map._calls.flyTo).toHaveLength(0);

    // the gesture ends well within the 3s bound → the command proceeds
    await vi.advanceTimersByTimeAsync(500);
    notifyUserGestureEnd();
    await vi.advanceTimersByTimeAsync(0); // let waitForGestureEnd resolve
    expect(map._calls.flyTo).toHaveLength(1);

    map._setViewport({ center: [116.0005, 39.0005], zoom: 12.02 });
    map._fire('moveend');
    await vi.advanceTimersByTimeAsync(0);
    await expect(promise).resolves.toMatchObject({ status: 'succeeded' });
  });

  it('resolves failed timeout after 10s if moveend never fires (queue cannot stall)', async () => {
    const map = makeMockMaplibreMap();
    const promise = viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));

    await vi.advanceTimersByTimeAsync(10_000);
    await expect(promise).resolves.toEqual({ status: 'failed', error: 'timeout' });
  });

  it('zoom_to_bbox fits the bbox and settles succeeded', async () => {
    const map = makeMockMaplibreMap();
    const promise = viewCommands.zoom_to_bbox.run(
      makeCtx(map, { bbox: [116, 39, 117, 40], padding: 40 }),
    );

    expect(map._calls.fitBounds).toEqual([
      // maxZoom:16 —— navigation.ts 对退化 bbox 的极端目标缩放防御
      { bbox: [116, 39, 117, 40], options: { padding: 40, duration: 1500, maxZoom: 16 } },
    ]);
    map._fire('moveend');
    await vi.advanceTimersByTimeAsync(0);
    await expect(promise).resolves.toMatchObject({ status: 'succeeded' });
  });

  it('set_map_view keeps the current center when only zoom/bearing change', async () => {
    const map = makeMockMaplibreMap({ center: [100, 20], zoom: 5 });
    const promise = viewCommands.set_map_view.run(makeCtx(map, { zoom: 9, bearing: 45 }));

    expect(map._calls.flyTo).toHaveLength(1);
    expect(map._calls.flyTo[0]).toMatchObject({ center: [100, 20], zoom: 9, bearing: 45 });
    // the animation reached the requested zoom (within tolerance)
    map._setViewport({ zoom: 9, bearing: 45 });
    map._fire('moveend');
    await vi.advanceTimersByTimeAsync(0);
    await expect(promise).resolves.toMatchObject({ status: 'succeeded' });
  });

  it('set_map_view with NO effective camera params settles succeeded immediately (no moveend needed)', async () => {
    const map = makeMockMaplibreMap({ center: [100, 20], zoom: 5, bearing: 10, pitch: 20 });
    // empty params → nothing would move → settle right away with the CURRENT
    // camera as actual instead of stalling the queue 10s into a `timeout` ack.
    // run() returns a plain result (synchronous — no promise/timer in play).
    const result = viewCommands.set_map_view.run(makeCtx(map, {}));

    expect(result).toEqual({
      status: 'succeeded',
      result: { center: [100, 20], zoom: 5, bearing: 10, pitch: 20 },
    });
    expect(map._calls.flyTo).toHaveLength(0);
    expect(map._calls.stop).toHaveLength(0);
  });

  it('set_map_view center-only actually jumps to that center (was a no-op success)', async () => {
    const map = makeMockMaplibreMap({ center: [100, 20], zoom: 5, bearing: 10, pitch: 20 });
    const promise = viewCommands.set_map_view.run(makeCtx(map, { center: [110, 30] }));

    // ROUND-2: a center-only request must actually MOVE the camera — an instant
    // jumpTo (merge current zoom/bearing/pitch), never a fake no-op success.
    expect(map.jumpTo).toHaveBeenCalledWith({ center: [110, 30], zoom: 5, bearing: 10, pitch: 20 });
    expect(map._calls.flyTo).toHaveLength(0);
    // jumpTo fires moveend synchronously → the deferred settle resolves it
    await vi.advanceTimersByTimeAsync(0);

    await expect(promise).resolves.toEqual({
      status: 'succeeded',
      result: { center: [110, 30], zoom: 5, bearing: 10, pitch: 20 },
    });
  });

  it('converts invalid coordinates into a failed result (never rejects/throws)', async () => {
    const map = makeMockMaplibreMap();
    // navigation.flyTo validates coordinates and throws internally; the camera
    // wrapper converts that into a failed MapCommandResult.
    const promise = viewCommands.fly_to.run(makeCtx(map, { center: [999, 999], zoom: 12 }));
    await expect(promise).resolves.toMatchObject({ status: 'failed' });
  });

  it('runs multiple camera commands independently (each settles on its own moveend)', async () => {
    const map = makeMockMaplibreMap();
    const a = viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));
    const b = viewCommands.zoom_to_bbox.run(makeCtx(map, { bbox: [100, 20, 110, 30] }));
    expect(map._calls.stop).toHaveLength(2); // each self-interrupted
    expect(map._calls.flyTo).toHaveLength(1);
    expect(map._calls.fitBounds).toHaveLength(1);

    // b's self-interrupt stop() cut a's flyTo short — the mock models real
    // MapLibre stop() firing the interrupt moveend synchronously. a's settled
    // viewport never reached its requested target → honest `interrupted`
    // (previously both falsely acked SUCCEEDED).
    map._fire('moveend'); // b settles on its own moveend
    await vi.advanceTimersByTimeAsync(0);

    await expect(a).resolves.toEqual({ status: 'failed', error: 'interrupted' });
    await expect(b).resolves.toMatchObject({ status: 'succeeded' });
  });

  // ─── ROUND-2: camera interrupt race + target convergence ───────────────

  it('interrupt moveend fires before dragstart: an active interaction handler → superseded, never SUCCEEDED', async () => {
    const map = makeMockMaplibreMap();
    const promise = viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));
    expect(map._calls.flyTo).toHaveLength(1);

    // A user grab mid-flyTo: the HandlerManager activates synchronously (a real
    // grab sets dragPan.isActive() BEFORE the dragstart DOM event dispatches).
    // The gesture flag is NOT set yet — dragstart lands the next frame.
    map.dragPan = { isActive: () => true };
    // The grab stops the animation → interrupt moveend fires synchronously.
    map.stop();

    await vi.advanceTimersByTimeAsync(0);
    await expect(promise).resolves.toEqual({ status: 'failed', error: 'superseded_by_user' });
  });

  it('dragstart landing within the deferred frame also catches the race (moveend → dragstart ordering)', async () => {
    const map = makeMockMaplibreMap();
    const promise = viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));

    // The grab stops the animation — the interrupt moveend fires NOW.
    map.stop();
    // The dragstart event lands the next frame — before the deferred settle
    // check runs, the gesture flag is set.
    notifyUserGestureStart();

    await vi.advanceTimersByTimeAsync(0);
    await expect(promise).resolves.toEqual({ status: 'failed', error: 'superseded_by_user' });
    expect(map._calls.stop).toHaveLength(2); // command self-interrupt + grab interrupt
  });

  it('settles interrupted when a foreign programmatic move cut the flyTo short (tolerance vs requested target)', async () => {
    const map = makeMockMaplibreMap();
    const promise = viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));

    // A foreign programmatic fitBounds lands before our flyTo reaches the
    // requested [116, 39] @ z12 — the settled viewport is far from the target.
    map._setViewport({ center: [117, 40], zoom: 13 });
    map._fire('moveend');
    await vi.advanceTimersByTimeAsync(0);

    await expect(promise).resolves.toEqual({ status: 'failed', error: 'interrupted' });
  });

  it('settles succeeded when the settled viewport is at the backend tolerance boundary', async () => {
    const map = makeMockMaplibreMap();
    const promise = viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));

    // Just at the tolerance edge: center delta 0.001°, zoom delta 0.05.
    map._setViewport({ center: [116.001, 39.001], zoom: 12.05 });
    map._fire('moveend');
    await vi.advanceTimersByTimeAsync(0);

    await expect(promise).resolves.toMatchObject({ status: 'succeeded' });
  });

  // ─── #534: set_map_view 校验器必须覆盖 bearing/pitch-only（与 run body 同宽）──

  it('validator #534: bearing-only / pitch-only / any named dimension pass; empty rejected', () => {
    const { requiredParams } = viewCommands.set_map_view;
    // run body 支持的全部有效形状都要放行（否则后端成功、前端 invalid_params）
    expect(requiredParams({ bearing: 30 })).toBe(true);
    expect(requiredParams({ pitch: 60 })).toBe(true);
    expect(requiredParams({ bearing: 30, pitch: 60 })).toBe(true);
    expect(requiredParams({ zoom: 5 })).toBe(true);
    expect(requiredParams({ center: [116, 39] })).toBe(true);
    expect(requiredParams({ center: [116, 39], zoom: 5, bearing: 30, pitch: 60 })).toBe(true);
    // 无有效参数的 no-op（run body 快路径处理）不经过校验器放行即可
    expect(requiredParams({})).toBe(false);
    expect(requiredParams({ opacity: 0.5 })).toBe(false);
  });

  it('run #534: bearing-only actually flies to the requested bearing and converges', async () => {
    const map = makeMockMaplibreMap({ center: [100, 20], zoom: 5, bearing: 10, pitch: 20 });
    const promise = viewCommands.set_map_view.run(makeCtx(map, { bearing: 30 }));

    expect(map._calls.flyTo).toHaveLength(1);
    expect(map._calls.flyTo[0]).toMatchObject({ center: [100, 20], zoom: 5, bearing: 30, pitch: 20 });
    // 相机真正到达请求的 bearing（数值真值，非仅 ack）
    map._setViewport({ bearing: 30 });
    map._fire('moveend');
    await vi.advanceTimersByTimeAsync(0);
    await expect(promise).resolves.toMatchObject({ status: 'succeeded' });
  });

  it('run #534: pitch-only preserves current center/zoom/bearing and converges', async () => {
    const map = makeMockMaplibreMap({ center: [100, 20], zoom: 5, bearing: 10, pitch: 0 });
    const promise = viewCommands.set_map_view.run(makeCtx(map, { pitch: 60 }));

    expect(map._calls.flyTo).toHaveLength(1);
    expect(map._calls.flyTo[0]).toMatchObject({ center: [100, 20], zoom: 5, bearing: 10, pitch: 60 });
    map._setViewport({ pitch: 60 });
    map._fire('moveend');
    await vi.advanceTimersByTimeAsync(0);
    await expect(promise).resolves.toMatchObject({ status: 'succeeded' });
  });
});
