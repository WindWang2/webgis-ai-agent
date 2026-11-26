import { describe, expect, it, vi, beforeEach } from 'vitest';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import { ApiError } from '@/lib/api/transport';
import {
  buildPreviewMapSpec,
  committedLayerBbox,
  defaultFlowConfig,
  fetchFlowRefGeoJSON,
  findAppliedFlow,
  flowConfigFromApplied,
  flowErrorDetail,
  flowFamilyLayerIds,
  flowStatsView,
  guessFieldBindings,
  inspectFlowSource,
  listFlowSourceCandidates,
  modeAvailabilityView,
  previewFlowProduct,
  queryFlowProduct,
} from './flow-product';

vi.mock('@/lib/api/transport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/transport')>();
  return { ...actual, apiFetch: vi.fn() };
});

import { apiFetch } from '@/lib/api/transport';
const apiFetchMock = vi.mocked(apiFetch);

const SPEC = {
  version: '1.0',
  sources: {
    'od-table': { type: 'geojson', url: 'ref:geojson-od-1' },
    'poi': { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } },
    'basemap': { type: 'raster', tiles: ['http://x/{z}/{x}/{y}.png'] },
    'flow-odf_x-edges-source': { type: 'geojson', url: 'ref:geojson-flow-edges' },
    'flow-odf_x-nodes-source': { type: 'geojson', url: 'ref:geojson-flow-nodes' },
  },
  layers: [
    {
      id: 'flow-odf_x-edges',
      type: 'line',
      source: 'flow-odf_x-edges-source',
      flow: {
        schema_version: 'od_flow.v1',
        product_ref: 'ref:flow-product-abc',
        family_id: 'odf_x',
        source_ref: 'ref:geojson-od-1',
        source_revision: 3,
        source_fingerprint: 'sha256:a',
        product_fingerprint: 'sha256:b',
        direction_mode: 'net',
        geometry: 'curve',
        show_arrows: true,
        weight_field: 'weight',
        source_weight_field: 'trips',
        weight_unit: '人次',
        supported_modes: ['directed', 'undirected-total'],
        node_totals_scope: 'thresholded',
        encoding: { weight_transform: 'sqrt', weight_domain: [1, 900], width_min_px: 2, width_max_px: 10 },
        filters: { top_n: 200, min_weight: 5 },
      },
    },
    { id: 'poi-layer', type: 'circle', source: 'poi' },
  ],
} as unknown as MapSpec;

beforeEach(() => apiFetchMock.mockReset());

describe('listFlowSourceCandidates', () => {
  it('只列带 ref 的 geojson 源，排除 flow 产物源与非 geojson 源', () => {
    const candidates = listFlowSourceCandidates(SPEC);
    expect(candidates).toEqual([{ key: 'od-table', ref: 'ref:geojson-od-1' }]);
  });

  it('空 spec 给空候选', () => {
    expect(listFlowSourceCandidates(null)).toEqual([]);
    expect(listFlowSourceCandidates(undefined)).toEqual([]);
  });
});

describe('findAppliedFlow / flowConfigFromApplied', () => {
  it('从 committed layer.flow 恢复已应用产品', () => {
    const applied = findAppliedFlow(SPEC);
    expect(applied?.layerId).toBe('flow-odf_x-edges');
    expect(applied?.flow.product_ref).toBe('ref:flow-product-abc');
  });

  it('恢复可编辑配置（显式绑定/口径保留，其余缺省）', () => {
    const config = flowConfigFromApplied(findAppliedFlow(SPEC)!.flow);
    expect(config.od_table_ref).toBe('ref:geojson-od-1');
    expect(config.weight_field).toBe('trips');
    expect(config.weight_unit).toBe('人次');
    expect(config.direction_mode).toBe('net');
    expect(config.geometry).toBe('curve');
    expect(config.weight_transform).toBe('sqrt');
    expect(config.weight_domain).toEqual([1, 900]);
    expect(config.width_min_px).toBe(2);
    expect(config.width_max_px).toBe(10);
    expect(config.top_n).toBe(200);
    expect(config.min_weight).toBe(5);
    expect(config.node_totals_scope).toBe('thresholded');
  });

  it('无 flow 层返回 null', () => {
    expect(findAppliedFlow({ version: '1', sources: {}, layers: [] })).toBeNull();
    expect(findAppliedFlow(null)).toBeNull();
  });
});

describe('guessFieldBindings', () => {
  it('精确名优先，其次子串；无命中给空串（不伪造）', () => {
    const guess = guessFieldBindings({
      ORIGIN_ID: 'str', from_lng: 'float', origin_lat: 'float',
      destination_id: 'str', d_lng: 'float', destination_lat: 'float',
      trips: 'int', note: 'str',
    });
    expect(guess.origin_id_field).toBe('ORIGIN_ID');
    expect(guess.origin_lng_field).toBe('from_lng');
    expect(guess.origin_lat_field).toBe('origin_lat');
    expect(guess.destination_id_field).toBe('destination_id');
    expect(guess.destination_lng_field).toBe('d_lng');
    expect(guess.destination_lat_field).toBe('destination_lat');
    expect(guess.weight_field).toBe('trips');
    expect(guessFieldBindings({ foo: 'str' }).weight_field).toBe('');
  });
});

describe('buildPreviewMapSpec', () => {
  const fragment = {
    sources: {
      'flow-f-edges-source': { type: 'geojson', url: 'ref:geojson-e' },
      'flow-f-nodes-source': { type: 'geojson', url: 'ref:geojson-n' },
    },
    layers: [{ id: 'flow-f-edges', type: 'line', source: 'flow-f-edges-source' }],
  };

  it('url 源替换为真实 inlineData（编译器只读消费）', () => {
    const spec = buildPreviewMapSpec(fragment, { type: 'FeatureCollection', features: [{ id: 'e1' }] }, { type: 'FeatureCollection', features: [{ id: 'n1' }] });
    expect(spec.sources['flow-f-edges-source']).toMatchObject({ type: 'geojson', inlineData: { features: [{ id: 'e1' }] } });
    expect(spec.sources['flow-f-nodes-source']).toMatchObject({ inlineData: { features: [{ id: 'n1' }] } });
    expect(spec.sources['flow-f-edges-source']).not.toHaveProperty('url');
    expect(spec.layers).toHaveLength(1);
  });

  it('缺数据时保留 url（诚实缺数，不造假图）', () => {
    const spec = buildPreviewMapSpec(fragment, null, null);
    expect(spec.sources['flow-f-edges-source']).toMatchObject({ type: 'geojson', url: 'ref:geojson-e' });
    expect(spec.sources['flow-f-edges-source']).not.toHaveProperty('inlineData');
  });

  it('family_id 含 nodes 时 edges 源仍填 edgeGeoJSON（后缀判定）', () => {
    const tricky = {
      sources: {
        'flow-x_nodes-edges-source': { type: 'geojson', url: 'ref:geojson-e' },
        'flow-x_nodes-nodes-source': { type: 'geojson', url: 'ref:geojson-n' },
      },
      layers: [{ id: 'flow-x_nodes-edges', type: 'line', source: 'flow-x_nodes-edges-source' }],
    };
    const spec = buildPreviewMapSpec(tricky, { type: 'FeatureCollection', features: [{ id: 'e1' }] }, { type: 'FeatureCollection', features: [{ id: 'n1' }] });
    expect(spec.sources['flow-x_nodes-edges-source']).toMatchObject({ inlineData: { features: [{ id: 'e1' }] } });
    expect(spec.sources['flow-x_nodes-nodes-source']).toMatchObject({ inlineData: { features: [{ id: 'n1' }] } });
  });
});

describe('flowStatsView', () => {
  it('区分 gross retained 与 net magnitude coverage（不同名称）', () => {
    const view = flowStatsView({
      rows: { raw: 100, valid: 90, filtered: 40, excluded: 10 },
      edges: { aggregated: 30, thresholded: 20, displayed: 12 },
      gross: { population: 1000, displayed: 800, retained: 0.8 },
      net: { population_magnitude: 600, displayed_magnitude: 300, magnitude_coverage: 0.5 },
      nodes: { total: 9, displayed: 7 },
    });
    expect(view.rowsRaw).toBe(100);
    expect(view.edgesDisplayed).toBe(12);
    expect(view.grossRetained).toBe(0.8);
    expect(view.netMagnitudeCoverage).toBe(0.5);
  });

  it('缺失 coverage 给 null（UI 显示「未知」而非 0）', () => {
    const view = flowStatsView({ gross: { retained: null }, net: {} });
    expect(view.grossRetained).toBeNull();
    expect(view.netMagnitudeCoverage).toBeNull();
    expect(flowStatsView(undefined)).toEqual({});
  });
});

describe('modeAvailabilityView', () => {
  it('net 不可用时禁用并附后端理由与 unknown_pairs', () => {
    const modes = modeAvailabilityView(['directed', 'undirected-total'], {
      directed: { available: true, reason: 'raw_vectors_retained' },
      'undirected-total': { available: true },
      net: { available: false, reason: 'missing_reverse_observations', unknown_pairs: 12 },
    });
    expect(modes.find((m) => m.mode === 'net')).toMatchObject({
      available: false, reason: 'missing_reverse_observations', unknownPairs: 12,
    });
  });

  it('无 availability 时按 supported_modes 裁决', () => {
    const modes = modeAvailabilityView(['directed'], undefined);
    expect(modes.find((m) => m.mode === 'net')?.available).toBe(false);
    expect(modes.find((m) => m.mode === 'directed')?.available).toBe(true);
  });
});

describe('flowErrorDetail', () => {
  it('解析 ApiError detail {error,message}', () => {
    const err = new ApiError(404, 'Not Found', { detail: { error: 'FLOW_NODE_NOT_FOUND', message: '不在产品中' } });
    expect(flowErrorDetail(err)).toEqual({ code: 'FLOW_NODE_NOT_FOUND', message: '不在产品中' });
  });
  it('非该形态返回 null', () => {
    expect(flowErrorDetail(new ApiError(500, 'x', 'oops'))).toBeNull();
    expect(flowErrorDetail(new Error('x'))).toBeNull();
  });
});

describe('flow product API 边界', () => {
  it('inspect POST config 到会话端点', async () => {
    apiFetchMock.mockResolvedValueOnce({ success: true, operation: 'inspect', source_ref: 'ref:geojson-od-1', source_revision: 3, fields: {}, sample: [] });
    await inspectFlowSource('sid-1', defaultFlowConfig({ od_table_ref: 'ref:geojson-od-1' }), { ownerToken: 'tok' });
    const [url, init] = apiFetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/chat/sessions/sid-1/flow-product/inspect');
    expect(init?.method).toBe('POST');
    expect(init?.body).toMatchObject({ config: { od_table_ref: 'ref:geojson-od-1', direction_mode: 'directed' } });
    expect(init?.ownerToken).toBe('tok');
  });

  it('preview 透传 AbortSignal（取消语义）', async () => {
    apiFetchMock.mockResolvedValueOnce({ success: true });
    const controller = new AbortController();
    await previewFlowProduct('sid-1', defaultFlowConfig(), { signal: controller.signal });
    expect(apiFetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
  });

  it('query 带 kind/object_id/direction/分页', async () => {
    apiFetchMock.mockResolvedValueOnce({ success: true, kind: 'node' });
    await queryFlowProduct('sid-1', { product_ref: 'ref:flow-product-abc', kind: 'node', object_id: 'n1', direction: 'inbound', offset: 50, limit: 50 });
    expect(apiFetchMock.mock.calls[0][1]?.body).toEqual({
      product_ref: 'ref:flow-product-abc', kind: 'node', object_id: 'n1',
      direction: 'inbound', offset: 50, limit: 50,
    });
  });

  it('fetchFlowRefGeoJSON 走 /layers/data/{ref}?session_id=', async () => {
    apiFetchMock.mockResolvedValueOnce({ type: 'FeatureCollection', features: [] });
    await fetchFlowRefGeoJSON('sid-1', 'ref:geojson-e');
    const [url] = apiFetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/layers/data/ref%3Ageojson-e?session_id=sid-1');
  });
});

describe('committedLayerBbox', () => {
  it('读取 committed profile 范围，拒绝缺失或非有限坐标', () => {
    const spec = {
      ...SPEC,
      sources: {
        'flow-odf_x-edges-source': { type: 'geojson', ref_id: 'ref:geojson-flow-edges', profile: { bbox: [103.9, 30.4, 104.1, 30.6] } },
      },
    } as unknown as MapSpec;
    expect(committedLayerBbox(spec, 'flow-odf_x-edges')).toEqual([103.9, 30.4, 104.1, 30.6]);
    expect(committedLayerBbox(SPEC, 'flow-odf_x-edges')).toBeNull();
    expect(committedLayerBbox({
      ...spec,
      sources: { 'flow-odf_x-edges-source': { type: 'geojson', profile: { bbox: [0, 0, Number.NaN, 1] } } },
    } as unknown as MapSpec, 'flow-odf_x-edges')).toBeNull();
  });
});

describe('flowFamilyLayerIds', () => {
  it('只收集带 flow_family_id 的平铺层（点击查询面并入用）', () => {
    const spec = {
      ...SPEC,
      layers: [
        ...SPEC.layers,
        { id: 'flow-odf_x-nodes', type: 'circle', source: 'flow-odf_x-nodes-source', metadata: { flow_family_id: 'odf_x' } },
        { id: 'chengdu-od-input', type: 'line', source: 'od-table' },
      ],
    } as unknown as MapSpec;
    expect(flowFamilyLayerIds(spec)).toEqual(['flow-odf_x-nodes']);
    // 夹具的 edges 层只带 flow 合同、无 metadata.flow_family_id → 不收
    expect(flowFamilyLayerIds(SPEC)).toEqual([]);
    expect(flowFamilyLayerIds(null)).toEqual([]);
  });
});
