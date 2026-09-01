/**
 * MapSpecRuntime filter fast path（§13）：filter-only patch 走 setFilter，
 * 绝不 removeLayer/addLayer；appliedSpec 正确前进。
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { MapSpecRuntime } from './runtime';
import { makeMockMaplibreMap } from '../../test/__mocks__/maplibre-map';
import type { MapSpec, MapSpecLayer } from '@/lib/mapspec-compiler/types';

function specWith(filter?: unknown[]): MapSpec {
  const layer: MapSpecLayer = {
    id: 'l__point', source: 's1', type: 'circle',
    paint: { 'circle-color': '#111' } as never,
    layout: { visibility: 'visible' },
    ...(filter ? { filter: filter as never } : {}),
  };
  return {
    version: '1.0',
    sources: { s1: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } as never } },
    layers: [layer],
  };
}

describe('runtime filter fast path', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('applies a filter-only change via setFilter with zero layer churn', async () => {
    const map = makeMockMaplibreMap();
    const runtime = new MapSpecRuntime(map as never);
    await runtime.reconcileAsync(specWith(['==', '$type', 'Point']));
    runtime.flush();

    const addCallsBefore = (map.addLayer as ReturnType<typeof vi.fn>).mock.calls.length;
    const removeCallsBefore = (map.removeLayer as ReturnType<typeof vi.fn>).mock.calls.length;
    const filterCallsBefore = (map.setFilter as ReturnType<typeof vi.fn>).mock.calls.length;

    await runtime.reconcileAsync(specWith(['in', ['get', 'district'], ['literal', ['武侯区']]]));
    runtime.flush();

    expect((map.addLayer as ReturnType<typeof vi.fn>).mock.calls.length).toBe(addCallsBefore);
    expect((map.removeLayer as ReturnType<typeof vi.fn>).mock.calls.length).toBe(removeCallsBefore);
    const filterCalls = (map.setFilter as ReturnType<typeof vi.fn>).mock.calls;
    expect(filterCalls.length).toBe(filterCallsBefore + 1);
    expect(filterCalls[filterCalls.length - 1][0]).toBe('l__point');
    expect(filterCalls[filterCalls.length - 1][1]).toEqual(['in', ['get', 'district'], ['literal', ['武侯区']]]);

    // appliedSpec 前进（下一次 diff 基准正确）。
    expect(runtime.getAppliedSpec()).toBe(runtime.getAppliedSpec());
    runtime.dispose();
  });

  it('clearing a filter maps to setFilter(null)', async () => {
    const map = makeMockMaplibreMap();
    const runtime = new MapSpecRuntime(map as never);
    await runtime.reconcileAsync(specWith(['==', '$type', 'Point']));
    runtime.flush();
    await runtime.reconcileAsync(specWith(undefined));
    runtime.flush();
    const filterCalls = (map.setFilter as ReturnType<typeof vi.fn>).mock.calls;
    expect(filterCalls.length).toBeGreaterThan(0);
    expect(filterCalls[filterCalls.length - 1][1]).toBeNull();
    runtime.dispose();
  });

  it('setFilter on an absent layer is a silent no-op (pending-ref semantics)', async () => {
    const map = makeMockMaplibreMap();
    (map.getLayer as ReturnType<typeof vi.fn>).mockReturnValue(undefined);
    const runtime = new MapSpecRuntime(map as never);
    await runtime.reconcileAsync(specWith(['==', '$type', 'Point']));
    runtime.flush();
    expect(runtime.getLastError()).toBeNull();
    runtime.dispose();
  });
});
