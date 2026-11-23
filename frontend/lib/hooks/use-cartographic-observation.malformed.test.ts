/**
 * P2 回归：观测去重键对降级载荷的健壮性。
 *
 * 旧实现只判 raster_image === undefined —— null/非字符串时 .length/.slice
 * 抛 TypeError；apply_ack.layers 非数组时 .filter 抛错；外层 async IIFE
 * 无 catch → unhandled rejection，整份观测静默不上行且无告警。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { MapSpecRuntime } from '@/lib/mapspec-runtime';

const collectMock = vi.hoisted(() => vi.fn());
const apiFetchMock = vi.hoisted(() => vi.fn());

vi.mock('@/lib/mapspec-runtime/render-observation', () => ({
  collectRenderObservation: collectMock,
  RuntimeErrorRing: class {
    push() {}
    drain() {}
  },
  waitForRenderSettle: vi.fn(async () => true),
}));

vi.mock('@/lib/api/transport', () => ({
  apiFetch: apiFetchMock,
  ApiTimeoutError: class ApiTimeoutError extends Error {},
}));

vi.mock('@/lib/utils/logger', () => ({
  devOnly: { warn: vi.fn(), log: vi.fn(), error: vi.fn() },
}));

import { devOnly } from '@/lib/utils/logger';
import { useCartographicObservation } from './use-cartographic-observation';

function makeLayer(fingerprint: string) {
  return {
    id: 'L1', name: 'L1', type: 'vector', visible: true, opacity: 1,
    group: 'analysis', _mapspecFingerprint: fingerprint, _mapspecGenerationAt: 1,
  } as never;
}

async function flushAsync(): Promise<void> {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

function issueObservation(sessionId: string) {
  const hook = renderHook(() => useCartographicObservation({
    runtimeRef: { current: null as MapSpecRuntime | null },
    sessionId,
    ownerToken: null,
    dispatchAction: vi.fn(),
  }));
  hook.result.current({
    map: { getStyle: () => ({ layers: [] }) } as never,
    spec: { layers: [] } as never,
    layers: [makeLayer(`fp-${sessionId}`)],
  });
  return hook;
}

describe('useCartographicObservation malformed observation payloads (P2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiFetchMock.mockResolvedValue({ observation_accepted: false });
  });

  it('still posts when raster_image is null and apply_ack.layers is not an array', async () => {
    collectMock.mockImplementation(() => ({
      mapspec_fingerprint: 'fp-null-raster',
      observed_at: 1,
      perf: {},
      raster_image: null,
      apply_ack: { status: 'ok', layers: null },
    }));
    issueObservation('sid-malformed');
    await flushAsync();
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
  });

  it('warns instead of dropping the observation as an unhandled rejection', async () => {
    collectMock.mockImplementation(() => {
      throw new Error('collect boom');
    });
    issueObservation('sid-collect-error');
    await flushAsync();
    expect(apiFetchMock).not.toHaveBeenCalled();
    expect(vi.mocked(devOnly.warn)).toHaveBeenCalledWith(
      '[map] cartographic observation collection failed:',
      expect.any(Error),
    );
  });
});
