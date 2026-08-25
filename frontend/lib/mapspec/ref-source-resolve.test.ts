import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { MapSpec, MapSpecLayer } from '@/lib/mapspec-compiler/types';
import { composeLiveMapSpec } from '@/lib/mapspec/live-spec';
import type { HudToSpecInput } from '@/lib/mapspec-runtime/adapter';
import {
  getRefSourcesGeneration,
  injectResolvedRefSources,
  isRefOnlySource,
  resetRefSourceCache,
  subscribeRefSources,
} from '@/lib/mapspec/ref-source-resolver';
import { resetLiveState } from '@/lib/mapspec/session-cursor';

/**
 * product 图层 ref 源解析契约（2026-08-25：webgis_map_product 直写层的
 * `source "webgis_map_product_layer_source" not found`）。两条通道：
 *  1. live-spec：HUD 图层已拉取的同 ref 数据按 ref_id 身份并入 ref-only 源；
 *  2. ref-source-resolver：无 HUD 挂靠的 ref 由 /layers/data 拉取兜底。
 */

vi.mock('@/lib/api/transport', () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from '@/lib/api/transport';
const apiFetchMock = vi.mocked(apiFetch);

function layer(id: string, source: string, type: MapSpecLayer['type'] = 'circle'): MapSpecLayer {
  return { id, source, type, paint: { color: '#fff' } } as unknown as MapSpecLayer;
}

const POI_FC = {
  type: 'FeatureCollection',
  features: [
    { type: 'Feature', geometry: { type: 'Point', coordinates: [104, 30] }, properties: { id: 1 } },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  resetRefSourceCache();
  resetLiveState();
});

describe('isRefOnlySource', () => {
  it('识别 {type:geojson, ref_id} 而排除一切可应用载荷形态', () => {
    expect(isRefOnlySource({ type: 'geojson', ref_id: 'ref:a' } as any)).toBe(true);
    expect(isRefOnlySource({ type: 'geojson', ref: 'ref:a' } as any)).toBe(true);
    expect(isRefOnlySource({ type: 'geojson', ref_id: 'ref:a', inlineData: POI_FC } as any)).toBe(false);
    expect(isRefOnlySource({ type: 'geojson', ref_id: 'ref:a', url: '/tiles' } as any)).toBe(false);
    expect(isRefOnlySource({ type: 'vector', tiles: ['http://t/{z}/{x}/{y}'] } as any)).toBe(false);
    expect(isRefOnlySource({ type: 'geojson', inlineData: POI_FC } as any)).toBe(false);
    expect(isRefOnlySource(undefined)).toBe(false);
  });
});

describe('live-spec mergeHudSources — ref_id 身份合并', () => {
  const committed: MapSpec = {
    version: '1.0',
    sources: {
      'webgis_map_product_layer_source': {
        type: 'geojson',
        ref_id: 'ref:geojson-poi',
        profile: { featureCount: 1 },
      } as any,
      resolved: { type: 'geojson', inlineData: POI_FC } as any,
    },
    layers: [
      layer('product-abc-heatmap', 'webgis_map_product_layer_source', 'heatmap'),
      layer('L1__point', 'resolved'),
    ],
  };

  function hudWith(fetched: boolean): HudToSpecInput {
    return {
      layers: [{
        id: 'ref:geojson-poi',
        name: '分析结果: query_local_poi',
        type: 'vector',
        visible: true,
        opacity: 1,
        group: 'analysis',
        source: fetched
          ? POI_FC
          : { type: 'FeatureCollection', features: [], metadata: { ref_id: 'ref:geojson-poi' } },
        _refId: 'ref:geojson-poi',
        _mapspecLayerId: 'result-chatcmpl-tool-x',
      } as any],
      processLayers: {},
      activeFilters: {},
      is3D: false,
    };
  }

  it('HUD 数据落地后按 ref_id 并入 ref-only 源（product 图层免二次下载）', () => {
    const spec = composeLiveMapSpec(committed, hudWith(true));
    const merged = spec.sources['webgis_map_product_layer_source'] as any;
    expect(merged.inlineData).toEqual(POI_FC);
    // 载荷互斥：并入 inlineData 后 url/dataPath 不残留
    expect(merged.url).toBeUndefined();
    expect(merged.dataPath).toBeUndefined();
  });

  it('HUD 未拉回数据（空占位）时不并入——保持 ref-only 等待', () => {
    const spec = composeLiveMapSpec(committed, hudWith(false));
    const merged = spec.sources['webgis_map_product_layer_source'] as any;
    expect(merged.inlineData).toBeUndefined();
    expect(merged.ref_id).toBe('ref:geojson-poi');
  });

  it('已有载荷的源不被 ref 合并改写', () => {
    const spec = composeLiveMapSpec(committed, hudWith(true));
    expect((spec.sources.resolved as any).inlineData).toEqual(POI_FC);
  });
});

describe('ref-source-resolver — 兜底拉取与注入', () => {
  const spec: MapSpec = {
    version: '1.0',
    sources: {
      orphan: { type: 'geojson', ref_id: 'ref:geojson-orphan', profile: { featureCount: 3 } } as any,
      owned: { type: 'geojson', ref_id: 'ref:geojson-owned' } as any,
      huge: { type: 'geojson', ref_id: 'ref:geojson-huge', profile: { featureCount: 99999 } } as any,
    },
    layers: [layer('p1', 'orphan'), layer('p2', 'owned'), layer('p3', 'huge')],
  };

  it('HUD 挂靠的 ref 不触发兜底拉取（避免双下载）', () => {
    injectResolvedRefSources(spec, { sessionId: 's1', ownerToken: null }, new Set(['ref:geojson-owned']));
    // orphan 与 huge 触发拉取（huge 超限直接放弃），owned 不拉
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(apiFetchMock.mock.calls[0][0]).toContain(encodeURIComponent('ref:geojson-orphan'));
  });

  it('无会话上下文不拉取、原样返回 spec', () => {
    const out = injectResolvedRefSources(spec, null);
    expect(apiFetchMock).not.toHaveBeenCalled();
    expect(out).toBe(spec);
  });

  it('拉取成功 → generation bump → 缓存命中注入 inlineData', async () => {
    apiFetchMock.mockResolvedValueOnce(POI_FC);
    let notified = 0;
    const unsub = subscribeRefSources(() => { notified += 1; });
    const gen0 = getRefSourcesGeneration();

    injectResolvedRefSources(spec, { sessionId: 's1', ownerToken: 'tok' });
    await vi.waitFor(() => expect(notified).toBeGreaterThan(0));

    expect(getRefSourcesGeneration()).toBeGreaterThan(gen0);
    expect(apiFetchMock.mock.calls[0][0]).toContain('session_id=s1');
    // ownerToken 透传（匿名会话 SEC-08）
    expect((apiFetchMock.mock.calls[0] as any)[1].ownerToken).toBe('tok');

    const out = injectResolvedRefSources(spec, { sessionId: 's1', ownerToken: null });
    expect((out.sources.orphan as any).inlineData).toEqual(POI_FC);
    // 未触及的源对象保持引用共享
    expect(out.sources.owned).toBe(spec.sources.owned);
    unsub();
  });

  it('拉取失败 → 失败标记，不重复拉取', async () => {
    apiFetchMock.mockRejectedValueOnce(new Error('boom'));
    injectResolvedRefSources(spec, { sessionId: 's1', ownerToken: null });
    await vi.waitFor(() => expect(getRefSourcesGeneration()).toBeGreaterThan(0));
    apiFetchMock.mockClear();
    injectResolvedRefSources(spec, { sessionId: 's1', ownerToken: null });
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it('超上限的 ref 放弃拉取并不再告警重复', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    injectResolvedRefSources(spec, { sessionId: 's1', ownerToken: null });
    const callsWithHuge = apiFetchMock.mock.calls.filter((c) => String(c[0]).includes('huge'));
    expect(callsWithHuge).toHaveLength(0);
    injectResolvedRefSources(spec, { sessionId: 's1', ownerToken: null });
    expect(warnSpy.mock.calls.filter((w) => String(w[0]).includes('huge'))).toHaveLength(1);
    warnSpy.mockRestore();
  });
});
