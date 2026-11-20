/**
 * RasterSamplingPanel 组件测试（G39 / WP11）。
 *
 * 覆盖：describe→选源/波段→建计划→预览（有界 + 记账披露）→执行→成果
 * （分页表 + 多单位曲线 + 断口断线 + 抽稀披露）；曲/表 hover 同 sample_id；
 * 执行回执 refs 展示与下载入口；错误 toast。
 *
 * 与 field-compute-panel.test.tsx 同款：API/store/toast 全 mock；
 * useT 用键直回的确定性 translator（i18n 键完整性由 key-completeness 测试锁）。
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { RasterSamplingPanel } from './raster-sampling-panel';

const mockDescribeSources = vi.fn();
const mockCreatePlan = vi.fn();
const mockRevisePlan = vi.fn();
const mockPreviewPlan = vi.fn();
const mockExecutePlan = vi.fn();
const mockPlanStatus = vi.fn();
const mockFetchResults = vi.fn();
const mockCancelPlan = vi.fn();
const mockAddToast = vi.fn();
const mockFetchRefGeoJSON = vi.fn();
const mockAddLayer = vi.fn();
const mockUpdateLayer = vi.fn();
const mockRemoveLayer = vi.fn();
const mockSetViewport = vi.fn();

vi.mock('@/lib/api/raster-sampling', () => ({
  describeSources: (...a: unknown[]) => mockDescribeSources(...a),
  createPlan: (...a: unknown[]) => mockCreatePlan(...a),
  revisePlan: (...a: unknown[]) => mockRevisePlan(...a),
  previewPlan: (...a: unknown[]) => mockPreviewPlan(...a),
  executePlan: (...a: unknown[]) => mockExecutePlan(...a),
  planStatus: (...a: unknown[]) => mockPlanStatus(...a),
  fetchResults: (...a: unknown[]) => mockFetchResults(...a),
  cancelPlan: (...a: unknown[]) => mockCancelPlan(...a),
}));

vi.mock('@/lib/api/data-fabric', () => ({
  dataFabricApi: {
    fetchRefGeoJSON: (...a: unknown[]) => mockFetchRefGeoJSON(...a),
  },
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (sel: (s: unknown) => unknown) =>
    sel({ addLayer: mockAddLayer, updateLayer: mockUpdateLayer,
          removeLayer: mockRemoveLayer, setViewport: mockSetViewport }),
}));

vi.mock('@/components/ui/toast', () => ({
  useToastStore: (sel: (s: unknown) => unknown) => sel({ addToast: mockAddToast }),
}));

vi.mock('@/lib/i18n/useT', () => ({
  useT: () => (key: string) => key,
}));

vi.mock('@/components/data-delivery/delivery-dialog', () => ({
  DataDeliveryDialog: ({ open, source }: { open: boolean; source: unknown }) => (
    open ? <div data-testid="delivery-dialog" data-source={JSON.stringify(source)} /> : null
  ),
}));

function makeDescribe() {
  return {
    success: true,
    raster_refs: [
      { ref_id: 'ref:raster-a', alias: '温度栅格', kind: 'numeric_raster', verified: false },
      { ref_id: 'ref:raster-b', alias: '降雨栅格', kind: 'numeric_raster', verified: false },
    ],
    truncated: false,
    budgets: { max_sources: 4, max_bands_per_source: 8, max_stations: 50000,
               max_value_cells: 500000, max_preview_stations: 64 },
    methods: ['source_pixel', 'bilinear', 'bilinear_renormalized'],
    distance_modes: ['geodesic', 'planar_metric'],
    algorithm_version: 'g39.v1',
  };
}

function makePlan() {
  return {
    success: true,
    plan: {
      plan_id: 'rsp_abc123', name: '旅程', revision: 1,
      mode: 'source_points', method: 'source_pixel',
      station_spec: {}, sources: [], nodata_policy: 'strict',
      algorithm_version: 'g39.v1', params_digest: 'rsp:xyz',
    },
    stations: {
      count: 3, crs: 'EPSG:4326', mode: 'source_points',
      part_ids: [], part_breaks: 0, distance_mode: '', line_length_m: 0,
      notes: [], first_sample_ids: ['sp_abc_000000'],
    },
  };
}

function makePreview() {
  const row = (i: number, valid = true, reason: string | null = null) => ({
    sample_id: `sp_t_${String(i).padStart(6, '0')}`, station_index: i,
    parent_locator: `st-${i}`, source_ref: 'ref:raster-a', band: 1,
    band_label: 'band 1', station_x: 10.01, station_y: 19.99,
    station_crs: 'EPSG:4326', part_id: '', chainage_m: null,
    source_x: 1, source_y: 2, row: i, col: i,
    raw_value: valid ? 42.0 : null, raw_exact: null,
    physical_value: null, interpolated_value: null,
    value_domain: 'raw', method: 'source_pixel', unit: '',
    unit_status: 'unknown', valid, invalid_reason: reason,
    support_ratio: null, data_revision: 'fp:t',
  });
  return {
    success: true, plan_id: 'rsp_abc123', revision: 1, preview: true,
    station_selection: { requested_stations: 3, previewed_stations: 3, method: 'all' },
    rows_shown: 3, rows_truncated: false,
    rows: [row(0), row(1), row(2, false, 'masked')],
    read_metrics: {
      opens: 1, blocks_touched: 1, window_reads: 1, batches: 1,
      decoded_bytes: 65536, stations_processed: 3, block_shapes: {},
      is_tiled: {}, overview_used: false,
    },
    cost_estimate: { stations: 3, total_bands: 1, value_cells: 3 },
    coverage_preview: {
      requested_cells: 3, valid_cells: 2,
      invalid_by_reason: { masked: 1 },
    },
  };
}

function makeExecute() {
  // API（非工具）响应形：内联执行直接回 {receipt, idempotent_reuse}
  return {
    success: true, idempotent_reuse: false, job_id: '7',
    receipt: {
      execution_id: 'rx_abc', plan_id: 'rsp_abc123', plan_revision: 1,
      status: 'completed', method: 'source_pixel', station_count: 3,
      params_digest: 'rsp:xyz',
      coverage: { requested_cells: 3, valid_cells: 3, invalid_by_reason: {},
                  stations_requested: 3, stations_with_any_valid: 3 },
      outputs: {
        sample_table_ref: 'ref:fabric-parquet/deadbeef',
        station_layer_ref: 'ref:raster-sampling-1',
      },
      degraded: [], job_id: '7', completed_at: 1,
    },
  };
}

function makeResults() {
  return {
    success: true, execution_id: 'rx_abc', plan_id: 'rsp_abc123',
    receipt: {
      execution_id: 'rx_abc', plan_id: 'rsp_abc123', plan_revision: 1,
      status: 'completed', method: 'source_pixel', station_count: 3,
      params_digest: 'rsp:xyz',
      coverage: { requested_cells: 3, valid_cells: 3, invalid_by_reason: {},
                  stations_requested: 3, stations_with_any_valid: 3 },
      outputs: {
        sample_table_ref: 'ref:fabric-parquet/deadbeef',
        station_layer_ref: 'ref:raster-sampling-1',
      },
      degraded: [], job_id: null, completed_at: 1,
    },
    rows: [
      { sample_id: 'sp_t_000000', station_index: 0, parent_locator: 'st-0',
        source_ref: 'ref:raster-a', band: 1, band_label: 'band 1',
        station_x: 10.01, station_y: 19.99, station_crs: 'EPSG:4326',
        part_id: 'part-0', chainage_m: 0, source_x: 1, source_y: 2,
        row: 0, col: 0, raw_value: 1.5, raw_exact: null, physical_value: null,
        interpolated_value: null, value_domain: 'raw', method: 'source_pixel',
        unit: '', unit_status: 'unknown', valid: true, invalid_reason: null,
        support_ratio: null, data_revision: 'fp:t' },
      { sample_id: 'sp_t_000001', station_index: 1, parent_locator: 'st-1',
        source_ref: 'ref:raster-a', band: 1, band_label: 'band 1',
        station_x: 10.02, station_y: 19.98, station_crs: 'EPSG:4326',
        part_id: 'part-1', chainage_m: 0, source_x: 2, source_y: 3,
        row: 1, col: 1, raw_value: null, raw_exact: null, physical_value: null,
        interpolated_value: null, value_domain: 'raw', method: 'source_pixel',
        unit: '', unit_status: 'unknown', valid: false,
        invalid_reason: 'masked', support_ratio: null, data_revision: 'fp:t' },
    ],
    pagination: { page: 1, per_page: 50, filtered_rows: 2, total_rows: 2,
                  filters: {} },
    station_layer_ref: 'ref:raster-sampling-1',
    band_columns: {},
    curves: {
      bands: [
        { source_ref: 'ref:raster-a', band: 1, points: [
          { sid: 'sp_t_000000', x: 0, y: 1.5, part: 'part-0', gap: false },
          { sid: 'sp_t_000001', x: 1, y: null, part: 'part-1', gap: true },
        ] },
      ],
      thinning: { 'ref:raster-a#1': { method: 'none', kept: 2, total: 2 } },
    },
  };
}

function setup() {
  mockDescribeSources.mockResolvedValue(makeDescribe());
  return render(<RasterSamplingPanel sessionId="sess-1" ownerToken={null} />);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('RasterSamplingPanel', () => {
  it('describes sources on mount and lists raster refs', async () => {
    setup();
    await waitFor(() => expect(mockDescribeSources).toHaveBeenCalled());
    expect(await screen.findByText('温度栅格')).toBeInTheDocument();
  });

  it('create→preview→execute→results flow with refs and coverage', async () => {
    mockCreatePlan.mockResolvedValue(makePlan());
    mockPreviewPlan.mockResolvedValue(makePreview());
    mockExecutePlan.mockResolvedValue(makeExecute());
    mockFetchResults.mockResolvedValue(makeResults());
    setup();
    await screen.findByText('温度栅格');

    // 选源 + 点 ref + 建计划
    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[0], { target: { value: 'ref:raster-a' } });
    const inputs = screen.getAllByRole('textbox');
    const pointsInput = inputs.find(
      (el) => el.getAttribute('value') === '') as HTMLElement;
    fireEvent.change(pointsInput, { target: { value: 'ref:points-1' } });
    fireEvent.click(screen.getByText('createPlan'));
    await waitFor(() => expect(mockCreatePlan).toHaveBeenCalled());
    expect(await screen.findByText('rsp_abc123')).toBeInTheDocument();

    // 预览：有界行 + 记账披露
    fireEvent.click(screen.getByText('preview'));
    await waitFor(() => expect(mockPreviewPlan).toHaveBeenCalled());
    expect(await screen.findByText('st-0')).toBeInTheDocument();
    expect(screen.getByText(/decodedBytes/).textContent).toMatch(/66 KB/);

    // 执行：回执 refs + 覆盖
    fireEvent.click(screen.getByText('execute'));
    await waitFor(() => expect(mockExecutePlan).toHaveBeenCalled());
    expect(await screen.findByTestId('sample-table-ref')).toHaveTextContent(
      'ref:fabric-parquet/deadbeef');
    expect(mockFetchResults).toHaveBeenCalled();
    const stCells = await screen.findAllByText('st-1');
    expect(stCells.length).toBeGreaterThanOrEqual(1);
  });

  it('curves render gaps and thinning disclosure', async () => {
    mockCreatePlan.mockResolvedValue(makePlan());
    mockCreatePlan.mockResolvedValue(makePlan());
    mockFetchResults.mockResolvedValue(makeResults());
    setup();
    await screen.findByText('温度栅格');
    fireEvent.change(screen.getAllByRole('combobox')[0],
      { target: { value: 'ref:raster-a' } });
    const boxes = screen.getAllByRole('textbox');
    fireEvent.change(boxes.find(
      (el) => (el as HTMLInputElement).value === '') as HTMLElement,
      { target: { value: 'ref:points-1' } });
    fireEvent.click(screen.getByText('createPlan'));
    await waitFor(() => expect(mockCreatePlan).toHaveBeenCalled());
    await screen.findByText('rsp_abc123');
    fireEvent.click(screen.getByText('loadResults'));
    await waitFor(() => expect(mockFetchResults).toHaveBeenCalled());
    const curves = await screen.findByTestId('raster-sampling-curves');
    expect(curves.textContent).toContain('curveThinning');
    // SVG 曲线在场：有效点画圆、缺口点（y=null）不画
    expect(curves.querySelectorAll('svg').length).toBeGreaterThan(0);
    expect(curves.querySelectorAll('circle').length).toBe(1);
  });

  it('hover sync between table row and curve point via sample id', async () => {
    mockCreatePlan.mockResolvedValue(makePlan());
    mockFetchResults.mockResolvedValue(makeResults());
    setup();
    await screen.findByText('温度栅格');
    fireEvent.change(screen.getAllByRole('combobox')[0],
      { target: { value: 'ref:raster-a' } });
    fireEvent.change(screen.getAllByRole('textbox').find(
      (el) => (el as HTMLInputElement).value === '') as HTMLElement,
      { target: { value: 'ref:points-1' } });
    fireEvent.click(screen.getByText('createPlan'));
    await waitFor(() => expect(mockCreatePlan).toHaveBeenCalled());
    await screen.findByText('rsp_abc123');
    fireEvent.click(screen.getByText('loadResults'));
    await waitFor(() => expect(mockFetchResults).toHaveBeenCalled());
    const row = await screen.findByText('st-0');
    fireEvent.mouseEnter(row.closest('tr') as HTMLElement);
    // 同 sample_id 的曲线点亮（<title> 子元素携带 sid）
    const circles = Array.from(document.querySelectorAll('circle'));
    const match = circles.find(
      (c) => c.querySelector('title')?.textContent?.includes('sp_t_000000'));
    expect(match).toBeDefined();
    // hover 期间该点位高亮（r 放大 + 橙色）
    expect(match?.getAttribute('r')).toBe('3.5');
    expect(match?.getAttribute('fill')).toBe('rgb(249 115 22)');
  });

  it('row click locates station on the map', async () => {
    mockSetViewport.mockClear();
    mockCreatePlan.mockResolvedValue(makePlan());
    mockFetchResults.mockResolvedValue(makeResults());
    setup();
    await screen.findByText('温度栅格');
    fireEvent.change(screen.getAllByRole('combobox')[0],
      { target: { value: 'ref:raster-a' } });
    fireEvent.change(screen.getAllByRole('textbox').find(
      (el) => (el as HTMLInputElement).value === '') as HTMLElement,
      { target: { value: 'ref:points-1' } });
    fireEvent.click(screen.getByText('createPlan'));
    await waitFor(() => expect(mockCreatePlan).toHaveBeenCalled());
    await screen.findByText('rsp_abc123');
    fireEvent.click(screen.getByText('loadResults'));
    await waitFor(() => expect(mockFetchResults).toHaveBeenCalled());
    const row = await screen.findByText('st-0');
    fireEvent.click(row.closest('tr') as HTMLElement);
    await waitFor(() => expect(mockSetViewport).toHaveBeenCalled());
    const args = mockSetViewport.mock.calls[0];
    expect(args[0]).toEqual([10.01, 19.99]);
  });

  it('loads station layer through existing layer lane', async () => {
    mockFetchRefGeoJSON.mockResolvedValue({
      type: 'FeatureCollection',
      features: [{ type: 'Feature', id: 'sp_t_000000',
        geometry: { type: 'Point', coordinates: [10, 19] }, properties: {} }],
    });
    mockCreatePlan.mockResolvedValue(makePlan());
    mockFetchResults.mockResolvedValue(makeResults());
    setup();
    await screen.findByText('温度栅格');
    fireEvent.change(screen.getAllByRole('combobox')[0],
      { target: { value: 'ref:raster-a' } });
    fireEvent.change(screen.getAllByRole('textbox').find(
      (el) => (el as HTMLInputElement).value === '') as HTMLElement,
      { target: { value: 'ref:points-1' } });
    fireEvent.click(screen.getByText('createPlan'));
    await waitFor(() => expect(mockCreatePlan).toHaveBeenCalled());
    await screen.findByText('rsp_abc123');
    fireEvent.click(screen.getByText('loadResults'));
    await waitFor(() => expect(mockFetchResults).toHaveBeenCalled());
    fireEvent.click(await screen.findByText('loadStationLayer'));
    await waitFor(() => expect(mockFetchRefGeoJSON).toHaveBeenCalled());
    expect(mockAddLayer).toHaveBeenCalled();
    expect(mockUpdateLayer).toHaveBeenCalled();
    expect(mockRemoveLayer).not.toHaveBeenCalled();
  });

  it('download opens delivery dialog with fabric ref', async () => {
    mockCreatePlan.mockResolvedValue(makePlan());
    mockFetchResults.mockResolvedValue(makeResults());
    setup();
    await screen.findByText('温度栅格');
    fireEvent.change(screen.getAllByRole('combobox')[0],
      { target: { value: 'ref:raster-a' } });
    fireEvent.change(screen.getAllByRole('textbox').find(
      (el) => (el as HTMLInputElement).value === '') as HTMLElement,
      { target: { value: 'ref:points-1' } });
    fireEvent.click(screen.getByText('createPlan'));
    await waitFor(() => expect(mockCreatePlan).toHaveBeenCalled());
    await screen.findByText('rsp_abc123');
    fireEvent.click(screen.getByText('loadResults'));
    await waitFor(() => expect(mockFetchResults).toHaveBeenCalled());
    fireEvent.click(await screen.findByText('downloadTable'));
    const dialog = await screen.findByTestId('delivery-dialog');
    const source = JSON.parse(
      (dialog as HTMLElement).dataset.source || '{}');
    expect(source).toEqual({
      kind: 'fabric_parquet', session_id: 'sess-1',
      ref: 'ref:fabric-parquet/deadbeef',
    });
  });

  it('typed error surfaces as toast', async () => {
    mockCreatePlan.mockRejectedValue(new Error('BUDGET_EXCEEDED: too many'));
    setup();
    await screen.findByText('温度栅格');
    fireEvent.change(screen.getAllByRole('combobox')[0],
      { target: { value: 'ref:raster-a' } });
    fireEvent.change(screen.getAllByRole('textbox').find(
      (el) => (el as HTMLInputElement).value === '') as HTMLElement,
      { target: { value: 'ref:points-1' } });
    fireEvent.click(screen.getByText('createPlan'));
    await waitFor(() => expect(mockAddToast).toHaveBeenCalled());
    expect(mockAddToast.mock.calls[0][0]).toContain('BUDGET_EXCEEDED');
  });
});
