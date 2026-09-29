/**
 * C11：渲染 ABI 版本协商门 + addLayerSafe 显隐/zoom 门接线测试。
 *
 * - forward/未知版本 → 拒绝渲染（零 addLayer、保 last-good、lastError 落定）；
 * - authored `visible:false` → layout.visibility "none"（此前无消费的
 *   契约漂移收口）；
 * - visibility.min_zoom/max_zoom → 层级 minzoom/maxzoom；
 * - 已知版本/缺失 version → 行为与既有逐位一致（零额外开销）。
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { MapSpecRuntime } from './runtime';
import { makeMockMaplibreMap } from '../../test/__mocks__/maplibre-map';
import { _resetSceneEvidenceForTests } from './adapter';
import type { MapSpec, MapSpecLayer } from '@/lib/mapspec-compiler/types';

function specWith(layerOverrides: Partial<MapSpecLayer>, version?: string): MapSpec {
  const layer: MapSpecLayer = {
    id: 'l__fill', source: 's1', type: 'fill',
    paint: { 'fill-color': '#123456' } as never,
    ...layerOverrides,
  };
  return {
    ...(version !== undefined ? { version } : {}),
    sources: { s1: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } as never } },
    layers: [layer],
  } as MapSpec;
}

function addedLayerDefs(map: ReturnType<typeof makeMockMaplibreMap>): Array<Record<string, unknown>> {
  return (map.addLayer as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as Record<string, unknown>);
}

describe('C11 renderer ABI version gate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    _resetSceneEvidenceForTests();
  });

  it('forward version → 拒绝渲染（零 addLayer + lastError + 证据）', async () => {
    const map = makeMockMaplibreMap();
    const runtime = new MapSpecRuntime(map as never);
    await runtime.reconcileAsync(specWith({}, '2.0'));
    runtime.flush();
    expect(addedLayerDefs(map)).toHaveLength(0);
    expect(runtime.lastError).toBe('mapspec_forward_version');
    runtime.dispose();
  });

  it('forward version → 保 last-good：后续已知版本 spec 正常接替', async () => {
    const map = makeMockMaplibreMap();
    const runtime = new MapSpecRuntime(map as never);
    const good = specWith({}, '1.0');
    await runtime.reconcileAsync(good);
    runtime.flush();
    expect(addedLayerDefs(map)).toHaveLength(1);
    await runtime.reconcileAsync(specWith({}, '2.0'));
    runtime.flush();
    expect(addedLayerDefs(map)).toHaveLength(1); // 拒绝，不 churn
    // last-good 被新 spec 接替：appliedSpec 前进到 1.5 spec（同 id → patch
    // 语义，不走 addLayer —— 拒绝期间 diff 基准保持 good）。
    const next = specWith({ layout: { visibility: 'none' } }, '1.5');
    await runtime.reconcileAsync(next);
    runtime.flush();
    expect(runtime.getAppliedSpec()).toBe(next);
    runtime.dispose();
  });

  it('非字符串 version → mapspec_version_invalid 拒绝', async () => {
    const map = makeMockMaplibreMap();
    const runtime = new MapSpecRuntime(map as never);
    await runtime.reconcileAsync({ ...specWith({}, '1.5'), version: 3 as unknown as string });
    runtime.flush();
    expect(addedLayerDefs(map)).toHaveLength(0);
    expect(runtime.lastError).toBe('mapspec_version_invalid');
    runtime.dispose();
  });

  it('缺失 version（存量 spec）→ 行为不变正常渲染', async () => {
    const map = makeMockMaplibreMap();
    const runtime = new MapSpecRuntime(map as never);
    await runtime.reconcileAsync(specWith({}));
    runtime.flush();
    expect(addedLayerDefs(map)).toHaveLength(1);
    expect(runtime.lastError).toBeNull();
    runtime.dispose();
  });
});

describe('C11 addLayerSafe visibility/zoom gate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    _resetSceneEvidenceForTests();
  });

  it('authored visible:false → layout.visibility "none"', async () => {
    const map = makeMockMaplibreMap();
    const runtime = new MapSpecRuntime(map as never);
    await runtime.reconcileAsync(specWith({ visible: false }, '1.5'));
    runtime.flush();
    const def = addedLayerDefs(map)[0];
    expect((def.layout as Record<string, unknown>).visibility).toBe('none');
    runtime.dispose();
  });

  it('visibility.min_zoom/max_zoom → 层级 minzoom/maxzoom', async () => {
    const map = makeMockMaplibreMap();
    const runtime = new MapSpecRuntime(map as never);
    await runtime.reconcileAsync(specWith({
      visibility: { min_zoom: 8, max_zoom: 14 },
    }, '1.5'));
    runtime.flush();
    const def = addedLayerDefs(map)[0];
    expect(def.minzoom).toBe(8);
    expect(def.maxzoom).toBe(14);
    runtime.dispose();
  });

  it('street_detail_minzoom hint → minzoom 下限合成', async () => {
    const map = makeMockMaplibreMap();
    const runtime = new MapSpecRuntime(map as never);
    await runtime.reconcileAsync(specWith({
      visibility: { hints: { street_detail_minzoom: 11 } },
    }, '1.5'));
    runtime.flush();
    expect(addedLayerDefs(map)[0].minzoom).toBe(11);
    runtime.dispose();
  });

  it('无 visibility 块 → 零 minzoom/maxzoom（既有行为逐位一致）', async () => {
    const map = makeMockMaplibreMap();
    const runtime = new MapSpecRuntime(map as never);
    await runtime.reconcileAsync(specWith({}, '1.0'));
    runtime.flush();
    const def = addedLayerDefs(map)[0];
    expect('minzoom' in def).toBe(false);
    expect('maxzoom' in def).toBe(false);
    expect((def.layout as Record<string, unknown>).visibility).toBe('visible');
    runtime.dispose();
  });
});
