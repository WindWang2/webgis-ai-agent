import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

// Mock the transport so we can assert URL + control promise resolution.
const pending = new Map<string, (v: any) => void>();
vi.mock('@/lib/api/transport', () => ({
  apiFetch: vi.fn((path: string, opts?: { signal?: AbortSignal }) => {
    return new Promise((resolve, reject) => {
      pending.set(path, resolve);
      const onAbort = () => reject(new DOMException('aborted', 'AbortError'));
      if (opts?.signal?.aborted) return onAbort();
      opts?.signal?.addEventListener('abort', onAbort, { once: true });
    });
  }),
  isApiError: () => false,
}));

// vi.mock is hoisted, so this resolves to the mocked module.
import { apiFetch } from '@/lib/api/transport';
import { useHudStore } from '@/lib/store/useHudStore';
import { useResultDescriptor } from '@/lib/hooks/use-result-descriptor';
import type { AnalysisResult } from '@/lib/results/types';

const apiFetchMock = apiFetch as unknown as { mock: { calls: string[][] } };

let refSeq = 0;
function seedResult(id: string, ref: string): AnalysisResult {
  useHudStore.getState().captureStepResult({
    step_id: id,
    tool: 'hotspot_analysis',
    geojson_ref: ref,
    result: { success: true, summary: 'ok', bbox: [0, 0, 1, 1] },
  });
  return useHudStore.getState().results.find((r) => r.id === id)!;
}

const flush = () => Promise.resolve();

describe('useResultDescriptor — metadata-first, no full GeoJSON', () => {
  beforeEach(() => {
    useHudStore.getState().clearResults();
    pending.clear();
    (apiFetchMock.mock.calls as any).length = 0;
    refSeq += 1;
  });

  it('fetches the descriptor endpoint, never the full-data endpoint', async () => {
    const ref = `ref:geojson-ep-${refSeq}`;
    const result = seedResult('ep-1', ref);
    renderHook((p: { r: AnalysisResult }) => useResultDescriptor(p.r, 'sess', null), {
      initialProps: { r: result },
    });
    await vi.waitFor(() => expect(apiFetchMock.mock.calls.length).toBeGreaterThan(0));
    const path = apiFetchMock.mock.calls[0][0];
    expect(path).toContain('/layers/descriptor/');
    expect(path).toContain(encodeURIComponent(ref));
    expect(path).not.toContain('/layers/data/');
  });

  it('dedupes: re-mounting the same ref does not trigger a second fetch', async () => {
    const ref = `ref:geojson-dp-${refSeq}`;
    const result = seedResult('dp-1', ref);
    const { unmount } = renderHook((p: { r: AnalysisResult }) => useResultDescriptor(p.r, 'sess', null), {
      initialProps: { r: result },
    });
    await vi.waitFor(() => expect(apiFetchMock.mock.calls.length).toBe(1));
    // Resolve → caches the descriptor for (sess, ref).
    pending.get(apiFetchMock.mock.calls[0][0])?.({ feature_count: 10 });
    await flush();
    unmount();

    // Remount with a fresh result id but the SAME ref+session → cache hit, no new fetch.
    const result2 = seedResult('dp-2', ref);
    renderHook((p: { r: AnalysisResult }) => useResultDescriptor(p.r, 'sess', null), {
      initialProps: { r: result2 },
    });
    await flush();
    const encRef = encodeURIComponent(ref);
    const refCalls = apiFetchMock.mock.calls.filter((c) => c[0].includes(encRef));
    expect(refCalls).toHaveLength(1);
  });

  it('stale-response protection: a slow result A cannot overwrite result B', async () => {
    const refA = `ref:geojson-a-${refSeq}`;
    const refB = `ref:geojson-b-${refSeq}`;
    const a = seedResult('stale-A', refA);
    const b = seedResult('stale-B', refB);

    const { rerender } = renderHook((p: { r: AnalysisResult }) => useResultDescriptor(p.r, 'sess', null), {
      initialProps: { r: a },
    });
    await flush(); // A's effect fires apiFetch(refA)
    rerender({ r: b });
    await flush(); // B's effect fires apiFetch(refB) + aborts A's controller

    // Resolve B → B is enriched.
    const bPath = apiFetchMock.mock.calls.find((c) => c[0].includes(encodeURIComponent(refB)))?.[0];
    expect(bPath).toBeDefined();
    pending.get(bPath!)?.({ feature_count: 99 });

    // Resolve A late (its controller was aborted on the switch, so this is a no-op).
    const aPath = apiFetchMock.mock.calls.find((c) => c[0].includes(encodeURIComponent(refA)))?.[0];
    if (aPath) pending.get(aPath)?.({ feature_count: 7 });

    // B is enriched once the promise chain (apiFetch → fetchDescriptor → hook) settles.
    await vi.waitFor(() => {
      expect(useHudStore.getState().results.find((r) => r.id === 'stale-B')!.outputs[0].featureCount).toBe(99);
    });

    // A must never have been enriched — stale-response protection held.
    await vi.waitFor(() => {
      expect(useHudStore.getState().results.find((r) => r.id === 'stale-A')!.outputs[0].featureCount).toBeUndefined();
    });
  });
});
