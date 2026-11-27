import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  captureCurrentMap,
  mapCaptureAvailable,
  registerMapCapture,
  type MapCaptureResult,
} from './map-capture';

vi.mock('@/lib/map-kit/exporter', () => ({ captureMapCanvas: vi.fn() }));
vi.mock('@/lib/mapspec-runtime/render-observation', () => ({ waitForRenderSettle: vi.fn() }));

function capture(center: [number, number]): MapCaptureResult {
  return {
    blob: new Blob(['map'], { type: 'image/png' }),
    deviceWidth: 100,
    deviceHeight: 80,
    dpr: 1,
    viewport: { center, zoom: 10, bearing: 0, pitch: 0 },
  };
}

describe('map capture registration ownership', () => {
  beforeEach(() => registerMapCapture(null));
  afterEach(() => registerMapCapture(null));

  it('keeps the later map active when the earlier MapPanel releases', async () => {
    const firstResult = capture([0, 0]);
    const secondResult = capture([1, 1]);
    const releaseFirst = registerMapCapture(async () => firstResult);
    const releaseSecond = registerMapCapture(async () => secondResult);

    releaseFirst();

    expect(mapCaptureAvailable()).toBe(true);
    expect(await captureCurrentMap()).toBe(secondResult);

    releaseSecond();
    expect(mapCaptureAvailable()).toBe(false);
    expect(await captureCurrentMap()).toBeNull();
  });

  it('does not let an older registration release a newer use of the same function', async () => {
    const result = capture([2, 2]);
    const fn = async () => result;
    const releaseFirst = registerMapCapture(fn);
    const releaseSecond = registerMapCapture(fn);

    releaseFirst();

    expect(await captureCurrentMap()).toBe(result);
    releaseSecond();
    expect(mapCaptureAvailable()).toBe(false);
  });

  it('preserves the legacy explicit null clear behavior', async () => {
    registerMapCapture(async () => capture([3, 3]));
    expect(mapCaptureAvailable()).toBe(true);

    registerMapCapture(null);

    expect(mapCaptureAvailable()).toBe(false);
    expect(await captureCurrentMap()).toBeNull();
  });
});
