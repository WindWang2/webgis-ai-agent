import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const apiFetchMock = vi.hoisted(() => vi.fn());
const bridgeMock = vi.hoisted(() => ({
  send: vi.fn().mockResolvedValue(undefined),
  onEventCallback: null as any,
}));

vi.mock('./useMapBridge', () => ({
  useMapBridge: (...args: unknown[]) => {
    bridgeMock.onEventCallback = args[2] as typeof bridgeMock.onEventCallback;
    return bridgeMock;
  },
}));
vi.mock('@/lib/api/transport', () => ({
  apiFetch: apiFetchMock,
  isApiError: () => false,
}));
vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));
vi.mock('@/lib/utils/logger', () => ({ devOnly: { log: vi.fn(), warn: vi.fn(), error: vi.fn() }, safeError: vi.fn() }));

import { useSSEStream } from './use-sse-stream';
import { useHudStore } from '@/lib/store/useHudStore';
import { hudStateToMapSpec } from '@/lib/mapspec-runtime/adapter';

describe('Invariant: mounting large MVT layer leaves zero FC bytes (#667)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useHudStore.setState({ layers: [], results: [] });
    renderHook(() =>
      useSSEStream('sid-invariant', vi.fn(), { current: 'sid-invariant' }, vi.fn(), () => null, null, { current: null }),
    );
  });

  it('no implicit full-FC fetch for MVT-mounted layers', async () => {
    // Simulate SSE step_result with large descriptor
    const descriptor = {
      ref_id: 'ref:big-100k',
      feature_count: 100_000,
      point_count: 100_000,
      geometry_types: ['Point'],
      bbox: [0, 0, 1, 1],
      mvt_capable: true,
      estimated_bytes: 100_000 * 100 + 1024,
      content_hash: null,
    };
    act(() => {
      bridgeMock.onEventCallback?.({
        event: 'step_result',
        data: {
          task_id: 't1',
          step_id: 's1',
          tool: 'buffer_analysis',
          geojson_ref: 'ref:big-100k',
          ref_descriptor: descriptor,
          result: { success: true, summary: 'ok' },
        },
      });
    });
    // Let any microtasks settle (the fetch is async but should NOT be triggered)
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(apiFetchMock).not.toHaveBeenCalled();

    const layer = useHudStore.getState().layers.find(l => l.id === 'ref:big-100k');
    expect(layer).toBeDefined();
    // Zustand placeholder: empty features
    expect((layer!.source as any).features).toEqual([]);
    expect(layer!._tileUrl).toContain('/tiles/');
    expect(layer!._descriptor).toEqual(descriptor);

    // Worker bridge / MapLibre source: must be vector, not geojson with inline data
    const spec = hudStateToMapSpec({ layers: useHudStore.getState().layers, processLayers: {}, activeFilters: {}, is3D: false });
    expect(spec.sources['ref:big-100k']).toEqual(expect.objectContaining({ type: 'vector', tiles: expect.any(Array) }));
    expect((spec.sources['ref:big-100k'] as any).inlineData).toBeUndefined();
  });
});
