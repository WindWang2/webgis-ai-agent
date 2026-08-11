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

    // simulate the animation settling at a different viewport than requested
    map._setViewport({ center: [117, 40], zoom: 13, bearing: 5, pitch: 10 });
    map._fire('moveend');

    await expect(promise).resolves.toEqual({
      status: 'succeeded',
      result: { center: [117, 40], zoom: 13, bearing: 5, pitch: 10 },
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

  it('moveend while a user gesture is active → superseded (never succeeded)', async () => {
    const map = makeMockMaplibreMap();
    // Gesture starts before the command; waitForGestureEnd gives up after 3s
    // while the gesture is STILL active (never ended) — the command proceeds
    // with the human still owning the camera.
    notifyUserGestureStart();

    const promise = viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));
    await vi.advanceTimersByTimeAsync(3000); // wait bound exceeded; gesture still active
    expect(map._calls.flyTo).toHaveLength(1);

    // The interrupt moveend fires while isUserGesturing() is true (dragstart may
    // land after the interrupt moveend) → the human owns the camera → superseded.
    map._fire('moveend');

    await expect(promise).resolves.toEqual({ status: 'failed', error: 'superseded_by_user' });
  });

  it('waits out an active user gesture (bounded) before starting the animation', async () => {
    const map = makeMockMaplibreMap();
    notifyUserGestureStart();

    const promise = viewCommands.fly_to.run(makeCtx(map, { center: [116, 39], zoom: 12 }));
    // not started while the user owns the camera
    expect(map._calls.flyTo).toHaveLength(0);

    // wait bound exceeded (3s) → proceeds anyway
    await vi.advanceTimersByTimeAsync(3000);
    expect(map._calls.flyTo).toHaveLength(1);

    // the gesture finally ends → the animation is no longer superseded
    notifyUserGestureEnd();
    map._fire('moveend');
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
      { bbox: [116, 39, 117, 40], options: { padding: 40, duration: 1500 } },
    ]);
    map._fire('moveend');
    await expect(promise).resolves.toMatchObject({ status: 'succeeded' });
  });

  it('set_map_view keeps the current center when only zoom/bearing change', async () => {
    const map = makeMockMaplibreMap({ center: [100, 20], zoom: 5 });
    const promise = viewCommands.set_map_view.run(makeCtx(map, { zoom: 9, bearing: 45 }));

    expect(map._calls.flyTo).toHaveLength(1);
    expect(map._calls.flyTo[0]).toMatchObject({ center: [100, 20], zoom: 9, bearing: 45 });
    map._fire('moveend');
    await expect(promise).resolves.toMatchObject({ status: 'succeeded' });
  });

  it('set_map_view with no effective camera params settles succeeded immediately (no moveend needed)', async () => {
    const map = makeMockMaplibreMap({ center: [100, 20], zoom: 5, bearing: 10, pitch: 20 });
    // only `center` → nothing would move → settle right away with the CURRENT
    // camera as actual instead of stalling the queue 10s into a `timeout` ack.
    // run() returns a plain result (synchronous — no promise/timer in play).
    const result = viewCommands.set_map_view.run(makeCtx(map, { center: [110, 30] }));

    expect(result).toEqual({
      status: 'succeeded',
      result: { center: [100, 20], zoom: 5, bearing: 10, pitch: 20 },
    });
    expect(map._calls.flyTo).toHaveLength(0);
    expect(map._calls.stop).toHaveLength(0);
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

    map._fire('moveend');
    await expect(a).resolves.toMatchObject({ status: 'succeeded' });
    await expect(b).resolves.toMatchObject({ status: 'succeeded' });
  });
});
