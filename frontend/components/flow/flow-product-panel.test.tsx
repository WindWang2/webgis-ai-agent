/**
 * G21 Studio OD flow 面板组件行为测试。
 * mock 仅在网络边界（@/lib/flow/flow-product 的 API 函数 + commitMapSpecMutation），
 * 投影/统计/编译全走真实实现。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, act } from '@testing-library/react';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import {
  commitMapSpecDocument,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import { publishSelection, resetSelectionStore } from '@/lib/selection/selection-store';
import { FlowProductPanel } from './flow-product-panel';
import {
  inspectFlowSource,
  previewFlowProduct,
  queryFlowProduct,
  fetchFlowRefGeoJSON,
} from '@/lib/flow/flow-product';
import { commitMapSpecMutation, refreshMapSpecAfterExternalMutation } from '@/lib/mapspec/user-mutation';

vi.mock('@/lib/flow/flow-product', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/flow/flow-product')>()),
  inspectFlowSource: vi.fn(),
  previewFlowProduct: vi.fn(),
  queryFlowProduct: vi.fn(),
  fetchFlowRefGeoJSON: vi.fn(),
}));

vi.mock('@/lib/mapspec/user-mutation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/mapspec/user-mutation')>()),
  commitMapSpecMutation: vi.fn(),
  refreshMapSpecAfterExternalMutation: vi.fn(),
}));

const inspectMock = vi.mocked(inspectFlowSource);
const previewMock = vi.mocked(previewFlowProduct);
const queryMock = vi.mocked(queryFlowProduct);
const refGeoMock = vi.mocked(fetchFlowRefGeoJSON);
const commitMock = vi.mocked(commitMapSpecMutation);
const refreshMock = vi.mocked(refreshMapSpecAfterExternalMutation);

const SESSION = 'sid-flowpanel-1';

const CANDIDATE_SPEC = {
  version: '1.0',
  sources: { 'od-table': { type: 'geojson', url: 'ref:geojson-od-1' } },
  layers: [{ id: 'poi-layer', type: 'circle', source: 'od-table' }],
} as unknown as MapSpec;

const APPLIED_SPEC = {
  version: '1.0',
  sources: {
    'od-table': { type: 'geojson', url: 'ref:geojson-od-1' },
    'flow-odf_x-edges-source': { type: 'geojson', url: 'ref:geojson-flow-edges' },
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
        direction_mode: 'directed',
        geometry: 'straight',
        show_arrows: true,
        weight_field: 'weight',
        source_weight_field: 'trips',
        weight_unit: '人次',
        supported_modes: ['directed', 'undirected-total'],
        node_totals_scope: 'filtered',
      },
    },
  ],
} as unknown as MapSpec;

const INSPECT = {
  success: true,
  operation: 'inspect',
  source_ref: 'ref:geojson-od-1',
  source_revision: 3,
  source_kind: 'table',
  row_count: 120,
  crs: 'EPSG:4326',
  value_semantics: 'flow',
  fields: {
    origin_id: 'str', origin_lng: 'float', origin_lat: 'float',
    destination_id: 'str', destination_lng: 'float', destination_lat: 'float',
    trips: 'int',
  },
  sample: [],
  sample_truncated: true,
};

const PREVIEW = {
  success: true,
  operation: 'preview',
  product_ref: 'ref:flow-product-abc',
  family_id: 'odf_x',
  source_ref: 'ref:geojson-od-1',
  source_revision: 3,
  cache_hit: 'miss',
  aggregation_reused: false,
  edge_ref: 'ref:geojson-flow-edges',
  node_ref: 'ref:geojson-flow-nodes',
  stats: {
    rows: { raw: 120, valid: 118, filtered: 60, excluded: 2 },
    edges: { aggregated: 40, thresholded: 30, displayed: 20 },
    gross: { population: 1000, displayed: 800, retained: 0.8 },
    net: { population_magnitude: 600, displayed_magnitude: 300, magnitude_coverage: 0.5 },
    nodes: { total: 9, displayed: 7 },
  },
  supported_modes: ['directed', 'undirected-total'],
  mode_availability: {
    directed: { available: true, reason: 'raw_vectors_retained' },
    'undirected-total': { available: true, reason: 'raw_vectors_retained' },
    net: { available: false, reason: 'missing_reverse_observations', unknown_pairs: 12 },
  },
  warnings: ['w1'],
  degradations: [],
  can_apply: true,
  blocking_reasons: [],
  mapspec_fragment: {
    sources: {
      'flow-odf_x-edges-source': { type: 'geojson', url: 'ref:geojson-flow-edges' },
      'flow-odf_x-nodes-source': { type: 'geojson', url: 'ref:geojson-flow-nodes' },
    },
    layers: [
      { id: 'flow-odf_x-edges', type: 'line', source: 'flow-odf_x-edges-source' },
    ],
  },
  mapspec_revision: 7,
};

const EDGE_GEO = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      id: 'e1',
      geometry: { type: 'LineString', coordinates: [[104.0, 30.6], [104.1, 30.7]] },
      properties: { id: 'e1', weight: 10, weight_encoded: 0.5 },
    },
  ],
};
const NODE_GEO = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      id: 'n1',
      geometry: { type: 'Point', coordinates: [104.0, 30.6] },
      properties: { node_id: 'n1', name: '武侯区', radius_value: 1 },
    },
  ],
};

beforeEach(async () => {
  vi.clearAllMocks();
  resetSelectionStore();
  setMapSpecSessionCursor(SESSION, 5, 'tok');
  await new Promise((resolve) => setTimeout(resolve, 0));
  inspectMock.mockResolvedValue(INSPECT);
  refGeoMock.mockImplementation(async (_sid, ref) =>
    String(ref).includes('nodes') ? NODE_GEO : EDGE_GEO,
  );
});

describe('FlowProductPanel', () => {
  it('无候选源时给具体指导', () => {
    commitMapSpecDocument({ version: '1', sources: {}, layers: [] }, 5);
    render(<FlowProductPanel />);
    expect(screen.getByText(/没有带 ref 的 GeoJSON 源/)).toBeTruthy();
  });

  it('首次挂载自动 inspect ref_id 源且不触顶初始化顺序', async () => {
    commitMapSpecDocument({
      ...CANDIDATE_SPEC,
      sources: { 'od-table': { type: 'geojson', ref_id: 'ref:geojson-od-1' } },
    } as MapSpec, 5);
    render(<FlowProductPanel />);
    await waitFor(() => expect(inspectMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByLabelText('权重字段')).toHaveValue('trips'));
    expect(previewMock).not.toHaveBeenCalled();
  });

  it('选择源后 inspect 真实字段预填绑定，Preview 才计算，Apply 携带预览 revision', async () => {
    commitMapSpecDocument(CANDIDATE_SPEC, 5);
    previewMock.mockResolvedValue(PREVIEW);
    commitMock.mockResolvedValue({ success: true, status: 'committed', mutation_revision: 6 });
    render(<FlowProductPanel />);

    const sourceSelect = await screen.findByLabelText('OD 表（GeoJSON 源）');
    fireEvent.change(sourceSelect, { target: { value: 'ref:geojson-od-1' } });
    await waitFor(() => expect(inspectMock).toHaveBeenCalledTimes(2));
    // inspect 返回后自动预填（guessFieldBindings 真实投影）
    await waitFor(() => expect(screen.getByLabelText('权重字段')).toHaveValue('trips'));

    fireEvent.click(screen.getByRole('button', { name: '预览' }));
    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(1));
    // 统计真实渲染（gross retained 与 net coverage 是不同指标）
    expect(await screen.findByText(/80.0%/)).toBeTruthy();
    expect(screen.getByText(/50.0%/)).toBeTruthy();
    // 缩略图走真实编译器 + 真实 ref 数据
    await waitFor(() => expect(refGeoMock).toHaveBeenCalledTimes(2));
    expect(document.querySelector('[role="img"] svg')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '应用到地图' }));
    await waitFor(() => expect(commitMock).toHaveBeenCalledTimes(1));
    const body = commitMock.mock.calls[0][0] as Record<string, unknown>;
    expect(body).toMatchObject({
      intent: 'apply_flow_product',
      product_ref: 'ref:flow-product-abc',
      family_id: 'odf_x',
      expected_revision: 7, // 调用方显式携带的预览 revision（非游标缺省 5）
    });
    await waitFor(() => expect(refreshMock).toHaveBeenCalledWith(SESSION, 6));
  });

  it('预览后改配置 → 预览过期，Apply 禁用并提示', async () => {
    commitMapSpecDocument(CANDIDATE_SPEC, 5);
    previewMock.mockResolvedValue(PREVIEW);
    render(<FlowProductPanel />);
    const sourceSelect = await screen.findByLabelText('OD 表（GeoJSON 源）');
    fireEvent.change(sourceSelect, { target: { value: 'ref:geojson-od-1' } });
    await waitFor(() => expect(screen.getByLabelText('权重字段')).toHaveValue('trips'));
    fireEvent.click(screen.getByRole('button', { name: '预览' }));
    await screen.findByText(/80.0%/);

    fireEvent.change(screen.getByLabelText('top N 边'), { target: { value: '100' } });
    expect(screen.getByText(/预览已过期/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '应用到地图' })).toHaveProperty('disabled', true);
    expect(previewMock).toHaveBeenCalledTimes(1); // 滑控件不触发计算
  });

  it('net 不可用时按 mode_availability 禁用并说明', async () => {
    commitMapSpecDocument(CANDIDATE_SPEC, 5);
    previewMock.mockResolvedValue(PREVIEW);
    render(<FlowProductPanel />);
    const sourceSelect = await screen.findByLabelText('OD 表（GeoJSON 源）');
    fireEvent.change(sourceSelect, { target: { value: 'ref:geojson-od-1' } });
    fireEvent.click(await screen.findByRole('button', { name: '预览' }));
    await screen.findByText(/80.0%/);
    // 预览前 mode_availability 未知：net 默认可选；预览后拿到 availability 再禁用
    await waitFor(() => {
      const netRadio = screen.getByLabelText(/净流幅度/) as HTMLInputElement;
      expect(netRadio.disabled).toBe(true);
    });
    expect(screen.getByText(/missing_reverse_observations/)).toBeTruthy();
  });

  it('空结果/拒绝真实显示：can_apply=false 时禁用 Apply 并列出 blocking reasons', async () => {
    commitMapSpecDocument(CANDIDATE_SPEC, 5);
    previewMock.mockResolvedValue({
      ...PREVIEW,
      can_apply: false,
      blocking_reasons: ['empty_display_set'],
      stats: { ...PREVIEW.stats, edges: { aggregated: 0, thresholded: 0, displayed: 0 } },
    });
    render(<FlowProductPanel />);
    const sourceSelect = await screen.findByLabelText('OD 表（GeoJSON 源）');
    fireEvent.change(sourceSelect, { target: { value: 'ref:geojson-od-1' } });
    fireEvent.click(await screen.findByRole('button', { name: '预览' }));
    expect(await screen.findByText('empty_display_set')).toBeTruthy();
    expect(screen.getByRole('button', { name: '应用到地图' })).toHaveProperty('disabled', true);
  });

  it('预览错误真实显示', async () => {
    commitMapSpecDocument(CANDIDATE_SPEC, 5);
    previewMock.mockRejectedValue(new Error('boom'));
    render(<FlowProductPanel />);
    const sourceSelect = await screen.findByLabelText('OD 表（GeoJSON 源）');
    fireEvent.change(sourceSelect, { target: { value: 'ref:geojson-od-1' } });
    fireEvent.click(await screen.findByRole('button', { name: '预览' }));
    expect((await screen.findByRole('alert')).textContent).toContain('boom');
  });

  it('刷新后从 committed layer.flow 恢复已应用状态；编辑配置带回显式绑定', async () => {
    commitMapSpecDocument(APPLIED_SPEC, 5);
    render(<FlowProductPanel />);
    expect(screen.getByText('已应用到地图')).toBeTruthy();
    expect(screen.queryByLabelText('OD 表（GeoJSON 源）')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '编辑配置' }));
    await waitFor(() => expect(screen.getByLabelText('权重字段')).toHaveValue('trips'));
    expect(screen.getByLabelText('权重单位')).toHaveValue('人次');
  });

  it('节点焦点：SelectionContext → /query 分页边；方向切换重新查询', async () => {
    commitMapSpecDocument(APPLIED_SPEC, 5);
    queryMock.mockResolvedValue({
      success: true,
      kind: 'node',
      product_ref: 'ref:flow-product-abc',
      node: { node_id: 'n1', name: '武侯区', inbound: 3, outbound: 8, net: 5 },
      direction: 'both',
      edges: { items: [{ id: 'e1', origin_node_id: 'n1', destination_node_id: 'n2', weight: 10 }], offset: 0, limit: 50, matched_count: 1, next_offset: null },
    });
    render(<FlowProductPanel />);
    act(() => {
      publishSelection('select', {
        source: 'map',
        layer_id: 'flow-odf_x-nodes',
        feature_id: 'n1',
        selected_ids: ['n1'],
        selected_categories: [],
        id_field: '$id',
        artifact_ref: 'ref:flow-product-abc',
        properties: { flow_focus: 'node', flow_direction: 'both', name: '武侯区' },
      });
    });
    await waitFor(() => expect(queryMock).toHaveBeenCalledTimes(1));
    expect(queryMock.mock.calls[0][1]).toMatchObject({ kind: 'node', object_id: 'n1', direction: 'both' });
    expect(await screen.findByText(/武侯区/, undefined, { timeout: 3000 })).toBeTruthy();
    fireEvent.click(screen.getByLabelText('流入'));
    await waitFor(() => expect(queryMock).toHaveBeenCalledTimes(2));
    expect(queryMock.mock.calls[1][1]).toMatchObject({ direction: 'inbound' });
  });

  it('焦点对象缺失（404）给具体提示，不伪造数据', async () => {
    commitMapSpecDocument(APPLIED_SPEC, 5);
    const { ApiError } = await import('@/lib/api/transport');
    queryMock.mockRejectedValue(
      new ApiError(404, 'Not Found', { detail: { error: 'FLOW_NODE_NOT_FOUND', message: 'not in product' } }),
    );
    render(<FlowProductPanel />);
    act(() => {
      publishSelection('select', {
        source: 'map',
        layer_id: 'flow-odf_x-nodes',
        feature_id: 'nX',
        selected_ids: ['nX'],
        selected_categories: [],
        id_field: '$id',
        artifact_ref: 'ref:flow-product-abc',
        properties: { flow_focus: 'node' },
      });
    });
    expect(await screen.findByText(/不在当前显示的产品中/)).toBeTruthy();
  });
});
