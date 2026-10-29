/**
 * PrepareFlow 组件测试 —— 一屏「检查 → 映射确认 → 准备 → 入图」流（M7）。
 *
 * 覆盖：上传列表装载与选择、检查渲染（收据/发现/映射/样本）、低置信度
 * 必选闸（未选全禁用执行）、阻断禁用执行、准备执行链（plan→preview→
 * execute→bind）与收据渲染、入图按钮出现。API 全 mock（@/lib/api/*）。
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PrepareFlow } from './prepare-flow';

const mockInspect = vi.fn();
const mockCreatePlan = vi.fn();
const mockPreview = vi.fn();
const mockExecute = vi.fn();
const mockBind = vi.fn();
const mockListUploads = vi.fn();
const mockFetchRefGeoJSON = vi.fn();
const mockAddLayer = vi.fn();
const mockUpdateLayer = vi.fn();

vi.mock('@/lib/api/data-preparation', () => ({
  dataPreparationApi: {
    inspect: (...a: unknown[]) => mockInspect(...a),
    createPlan: (...a: unknown[]) => mockCreatePlan(...a),
    previewPlan: (...a: unknown[]) => mockPreview(...a),
    executePlan: (...a: unknown[]) => mockExecute(...a),
    bindFields: (...a: unknown[]) => mockBind(...a),
  },
}));

vi.mock('@/lib/api/upload', () => ({
  listUploads: (...a: unknown[]) => mockListUploads(...a),
}));

vi.mock('@/lib/api/data-fabric', () => ({
  dataFabricApi: {
    fetchRefGeoJSON: (...a: unknown[]) => mockFetchRefGeoJSON(...a),
  },
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (sel: (s: unknown) => unknown) =>
    sel({ addLayer: mockAddLayer, updateLayer: mockUpdateLayer }),
}));

vi.mock('@/components/ui/toast', () => ({
  useToastStore: (sel: (s: unknown) => unknown) => sel({ addToast: vi.fn() }),
}));

function makeInspection(overrides: Record<string, unknown> = {}) {
  return {
    input_ref: 'ref:data-1',
    input_fingerprint: 'dsd-v1:abc',
    receipt: {
      input_ref: 'ref:data-1',
      geometry_type: 'Point',
      srs: 'EPSG:4326',
      crs_source: 'declared',
      axis_order_guessed: 'lon_lat',
      coordinate_range: [104, 30, 104.1, 30.1],
      encoding: '',
      time_fields: [],
      tz_status: '',
      pk_field: '学校编号',
      pk_status: 'unique',
      row_count: 20,
      sample_rows_scanned: 20,
      findings: [
        { code: 'impossible_coordinates', severity: 'warn', message_zh: '2 行越界',
          evidence: {}, suggestion: 'filter_rows', field: '' },
      ],
      blocked: false,
    },
    proposals: [
      { field: '学校编号', dtype: 'string',
        candidates: [{ role: 'id', confidence: 0.95, reasons: ['name_suffix'] }],
        rejected: [], needs_clarification: false },
      { field: '学校名称', dtype: 'string',
        candidates: [{ role: 'name', confidence: 0.9, reasons: ['name_suffix'] }],
        rejected: [], needs_clarification: false },
      { field: '神秘列', dtype: 'string',
        candidates: [], rejected: [], needs_clarification: true },
    ],
    sample: [
      { properties: { 学校编号: 'S001', 学校名称: '第一小学' },
        geometry_type: 'Point', first_position: [104, 30] },
    ],
    sample_truncated: false,
    ...overrides,
  };
}

async function renderFlow() {
  const { I18nProvider } = await import('@/lib/i18n/i18n-provider');
  return render(
    <I18nProvider>
      <PrepareFlow sessionId="sess-1" ownerToken={null} />
    </I18nProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // 固定 locale：I18nProvider 初值读 window.__GEOAGENT_LOCALE__（jsdom 缺省
  // 无该键，与 add-source-form.test 同款钉扎）。
  (window as unknown as Record<string, unknown>).__GEOAGENT_LOCALE__ = 'zh-CN';
  mockListUploads.mockResolvedValue({
    total: 1,
    uploads: [{ session_ref: 'ref:data-1', original_name: 'schools.csv',
                feature_count: 20 }],
  });
});

describe('PrepareFlow', () => {
  it('装载上传列表并允许选择后检查', async () => {
    mockInspect.mockResolvedValue(makeInspection());
    await renderFlow();
    const select = await screen.findByTestId('dp-source-select');
    fireEvent.change(select, { target: { value: 'ref:data-1' } });
    fireEvent.click(screen.getByTestId('dp-inspect'));
    await waitFor(() => {
      expect(mockInspect).toHaveBeenCalledWith(
        { session_id: 'sess-1', source_ref: 'ref:data-1', user_hints: {} },
        { ownerToken: null },
      );
    });
    expect(await screen.findByTestId('dp-receipt')).toBeTruthy();
    expect(screen.getByTestId('dp-findings').children.length).toBe(1);
    expect(screen.getByTestId('dp-mapping')).toBeTruthy();
    expect(screen.getByTestId('dp-sample')).toBeTruthy();
  });

  it('低置信度字段未选全时禁用执行并给出指引', async () => {
    mockInspect.mockResolvedValue(makeInspection());
    await renderFlow();
    fireEvent.change(await screen.findByTestId('dp-source-select'),
                     { target: { value: 'ref:data-1' } });
    fireEvent.click(screen.getByTestId('dp-inspect'));
    const hint = await screen.findByTestId('dp-clarify-hint');
    expect(hint.textContent).toContain('神秘列');
    expect(screen.getByTestId('dp-prepare')).toBeDisabled();
    // 用户显式选择后闸门解除
    fireEvent.change(screen.getByTestId('dp-role-神秘列').querySelector('select')!,
                     { target: { value: 'category' } });
    await waitFor(() => {
      expect(screen.getByTestId('dp-prepare')).not.toBeDisabled();
    });
  });

  it('阻断发现禁用执行（绝不呈现可继续）', async () => {
    const inspection = makeInspection();
    inspection.receipt.findings = [
      { code: 'axis_order_suspect', severity: 'blocked', message_zh: '疑似对调',
        evidence: {}, suggestion: 'switch_xy', field: '' },
    ];
    inspection.receipt.blocked = true;
    mockInspect.mockResolvedValue(inspection);
    await renderFlow();
    fireEvent.change(await screen.findByTestId('dp-source-select'),
                     { target: { value: 'ref:data-1' } });
    fireEvent.click(screen.getByTestId('dp-inspect'));
    expect(await screen.findByTestId('dp-blocked-hint')).toBeTruthy();
    expect(screen.getByTestId('dp-prepare')).toBeDisabled();
  });

  it('准备执行链：plan → preview → execute → bind → 收据 + 入图按钮', async () => {
    mockInspect.mockResolvedValue(makeInspection());
    mockCreatePlan.mockResolvedValue({
      plan: { plan_id: 'prep-1', plan_hash: 'h1', state: 'proposed',
              input_ref: 'ref:data-1', input_fingerprint: 'dsd-v1:abc',
              steps: [], mutates_data: true },
      warnings: [],
    });
    mockPreview.mockResolvedValue({
      plan_id: 'prep-1', sampled_rows: 20, truncated: false,
      estimated_rows_after: 18, quality_delta_sample: { impossible_coordinates: -1 },
      steps: [], degraded: [],
    });
    mockExecute.mockResolvedValue({
      receipt: {
        plan_id: 'prep-1', plan_hash: 'h1', input_ref: 'ref:data-1',
        output_ref: 'ref:prep-9', output_fingerprint: 'dsd-v1:def',
        row_before: 20, row_after: 18,
        quality_before: { impossible_coordinates: 1 },
        quality_after: {}, degraded: [], user_confirmations: [],
      },
    });
    mockBind.mockResolvedValue({
      source_ref: 'ref:data-1', descriptor_fingerprint: 'dsd-v1:fff',
      bound_fields: ['学校编号'], skipped: [],
    });
    await renderFlow();
    fireEvent.change(await screen.findByTestId('dp-source-select'),
                     { target: { value: 'ref:data-1' } });
    fireEvent.click(screen.getByTestId('dp-inspect'));
    // 解决需澄清字段
    await waitFor(() => screen.getByTestId('dp-role-神秘列'));
    fireEvent.change(screen.getByTestId('dp-role-神秘列').querySelector('select')!,
                     { target: { value: 'category' } });
    const prepareBtn = await screen.findByTestId('dp-prepare');
    await waitFor(() => expect(prepareBtn).not.toBeDisabled());
    fireEvent.click(prepareBtn);
    const done = await screen.findByTestId('dp-receipt-done');
    expect(done.textContent).toContain('20');
    expect(done.textContent).toContain('18');
    expect(mockCreatePlan).toHaveBeenCalledTimes(1);
    // 映射步全部以用户确认口径进入计划
    const stepsArg = mockCreatePlan.mock.calls[0][0].steps;
    expect(stepsArg.filter((s: { type: string }) => s.type === 'field_mapping').length)
      .toBe(3);
    expect(mockBind).toHaveBeenCalled();
    // 入图
    mockFetchRefGeoJSON.mockResolvedValue({ type: 'FeatureCollection', features: [] });
    fireEvent.click(screen.getByTestId('dp-load'));
    await waitFor(() => {
      expect(mockAddLayer).toHaveBeenCalledWith(expect.objectContaining({
        _refId: 'ref:prep-9',
      }));
      expect(mockFetchRefGeoJSON).toHaveBeenCalledWith('ref:prep-9', 'sess-1',
                                                       { ownerToken: null });
    });
  });

  it('无会话时渲染提示且不发请求', async () => {
    const { I18nProvider } = await import('@/lib/i18n/i18n-provider');
    render(
      <I18nProvider>
        <PrepareFlow sessionId={null} />
      </I18nProvider>,
    );
    expect(mockListUploads).not.toHaveBeenCalled();
  });
});
