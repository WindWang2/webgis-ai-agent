/**
 * FieldComputePanel 组件测试（G19）。
 *
 * 覆盖：选数据→describe→写公式→预览→执行→结果/入图；参数变更使旧预览失效
 * （generation）；迟到响应按 generation 丢弃；编译错误展示 code/detail/position/
 * hint；键盘可操作（label / aria / Enter 提交 / 字段按钮）。
 *
 * 与 prepare-flow.test.tsx 同款：API / store / toast 全 mock。useT 用一个把
 * 「键 + 参数值」拼回字符串的确定性 translator mock —— 这样断言可落在真实数据
 * 值（错误码、ref、计数）上，而不依赖共享 messages.ts 是否已登记 fieldCompute
 * namespace（那部分由主 Agent 接线）。
 */
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { FieldComputePanel } from './field-compute-panel';

const mockDescribe = vi.fn();
const mockCreatePlan = vi.fn();
const mockPreview = vi.fn();
const mockExecute = vi.fn();
const mockGetResult = vi.fn();
const mockListUploads = vi.fn();
const mockFetchRefGeoJSON = vi.fn();
const mockAddLayer = vi.fn();
const mockUpdateLayer = vi.fn();

vi.mock('@/lib/api/field-compute', () => ({
  fieldComputeApi: {
    describe: (...a: unknown[]) => mockDescribe(...a),
    createPlan: (...a: unknown[]) => mockCreatePlan(...a),
    preview: (...a: unknown[]) => mockPreview(...a),
    execute: (...a: unknown[]) => mockExecute(...a),
    getResult: (...a: unknown[]) => mockGetResult(...a),
  },
  // 与真实实现同口径：从 {body:{detail:{...}}} 提取 code/detail/position/hint。
  parseFieldComputeError: (err: any) => {
    const body = err?.body;
    let node = body;
    if (node && typeof node === 'object' && 'detail' in node) node = node.detail;
    if (node && typeof node === 'object') {
      return {
        code: node.code,
        detail: node.detail || node.message || '',
        position: node.position,
        hint: node.hint,
      };
    }
    return { detail: typeof node === 'string' ? node : (err?.message || 'error') };
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

// 确定性 translator：把 key 与所有参数值拼回字符串，便于断言真实数据值。
vi.mock('@/lib/i18n/useT', () => ({
  useT: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}|${Object.values(params).join('|')}` : key,
}));

function makeDescribe(overrides: Record<string, unknown> = {}) {
  return {
    input_ref: 'ref:data-1',
    data_shape: 'tabular',
    row_count: 20,
    crs: 'EPSG:4326',
    crs_source: 'declared',
    fields: [
      { name: '人口', dtype: 'number', unit_hint: '', unit_confidence: 'undeclared', sample: [100, 200] },
      { name: '面积 平方千米', dtype: 'number', unit_hint: '', unit_confidence: 'undeclared', sample: [1, 2] },
    ],
    sample_rows: [{ '人口': 100, '面积 平方千米': 1 }],
    functions: [
      { name: 'abs', family: 'number', arg_types: ['number'], result_type: 'number',
        null_behavior: 'null', unit_rule: 'preserve', help_zh: 'abs help', min_args: 1, max_args: 1 },
    ],
    scope_modes: ['all_rows', 'predicate', 'selection'],
    error_policies: ['null', 'abort'],
    limits: { max_rows: 100000, max_ops_per_row: 50 },
    geometry: { mode: 'geodesic', block_reason: null, detail: '' },
    unit_declaration_required: true,
    function_set_version: 'g19-fc-1.0',
    ir_version: 'ir-1',
    ...overrides,
  };
}

function makePlan() {
  return {
    plan: {
      plan_id: 'fc-1', compute_fingerprint: 'cfp-abc', state: 'compiled',
      input_ref: 'ref:data-1', input_fingerprint: 'ifp', columns: [], mutates_data: false,
    },
    data_shape: 'tabular',
    column_order: ['密度'],
    scope_summary: { mode: 'all_rows' },
    geometry: { mode: 'geodesic', block_reason: null, detail: '' },
    estimated_ops: 40,
    warnings: [],
  };
}

function makePreview(generation = 0) {
  return {
    plan_id: 'fc-1', generation, compute_fingerprint: 'cfp-abc', kind: 'sample',
    sampled_rows: 5, total_rows: 20, truncated: true, data_shape: 'tabular',
    columns: [
      { name: '密度', formula: '人口 / 面积', dtype: 'number', unit: '', unit_status: 'undeclared',
        unit_dimension: '', measurement_kind: 'density', derived_from: {}, declared_unit: '',
        computed: 5, nulls: 0, errors: 0 },
    ],
    before_after: [{ row: 0, before: { 人口: 100 }, after: { 密度: 100 } }],
    null_count_sample: 0, error_count_sample: 0, error_samples: [],
    sample_error_rate_estimate: 0,
    scope: { mode: 'all_rows' },
    unit_summary: { 密度: { unit: '', unit_status: 'undeclared', dimension: '', display: '', declared: false } },
    precision_policy: 'ieee754',
    geometry: {},
    budget: { row_count: 20, estimated_ops: 40, max_rows: 100000, max_ops_per_row: 50, within_budget: true },
    degraded: [],
    confirmation: { params_fingerprint: 'cfp-abc', note: 'confirm note' },
    next_actions: ['execute'],
  };
}

function makeReceipt() {
  return {
    receipt: {
      plan_id: 'fc-1', compute_fingerprint: 'cfp-abc', input_ref: 'ref:data-1', output_ref: 'ref:fc-9',
      row_count: 20, computed_rows: 20, selected_rows: 20, skipped_rows: 0,
      null_count: 0, error_count: 0,
      columns: [], scope: { mode: 'all_rows' }, formula_summary: ['密度 = 人口 / 面积'],
      unit_summary: {}, precision_policy: 'ieee754', persistence: 'session_ref',
      descriptor_minted: true, degraded: [], created_at: 1,
    },
  };
}

function makeResult() {
  return {
    output_ref: 'ref:fc-9', data_shape: 'tabular', crs: 'EPSG:4326', row_count: 20,
    fields: ['人口', '面积 平方千米', '密度'],
    sample_rows: [{ 人口: 100, 面积: 1, 密度: 100 }],
    readable: true,
  };
}

async function renderPanel() {
  render(<FieldComputePanel sessionId="sess-1" ownerToken={null} />);
  const select = await screen.findByTestId('fc-source-select');
  fireEvent.change(select, { target: { value: 'ref:data-1' } });
  fireEvent.click(screen.getByTestId('fc-describe'));
  await screen.findByTestId('fc-schema');
}

function fillFormula(name = '密度', expr = '{人口} / {面积 平方千米}') {
  fireEvent.change(screen.getByTestId('fc-target-name'), { target: { value: name } });
  fireEvent.change(screen.getByTestId('fc-expression'), { target: { value: expr } });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockListUploads.mockResolvedValue({
    total: 1,
    uploads: [{ session_ref: 'ref:data-1', original_name: 'data.csv', feature_count: 20 }],
  });
  mockDescribe.mockResolvedValue(makeDescribe());
  mockCreatePlan.mockResolvedValue(makePlan());
  mockPreview.mockResolvedValue(makePreview(0));
  mockExecute.mockResolvedValue(makeReceipt());
  mockGetResult.mockResolvedValue(makeResult());
  mockFetchRefGeoJSON.mockResolvedValue({ type: 'FeatureCollection', features: [] });
});

describe('FieldComputePanel', () => {
  it('选数据→describe→写公式→预览→执行→结果表 + 入图', async () => {
    await renderPanel();
    expect(screen.getByTestId('fc-field-人口')).toBeTruthy();
    expect(screen.getByTestId('fc-functions').tagName).toBe('DETAILS');

    fillFormula();
    fireEvent.submit(screen.getByTestId('fc-form'));

    const pv = await screen.findByTestId('fc-preview-view');
    expect(pv.textContent).toContain('100'); // before/after 样本真实值
    expect(screen.getByTestId('fc-sample-badge')).toBeTruthy(); // sample/estimate 标注
    expect(mockCreatePlan).toHaveBeenCalledTimes(1);
    expect(mockPreview).toHaveBeenCalledWith('fc-1', 'sess-1', expect.any(Number), expect.anything());

    // 确认执行：expected_fingerprint 绑定已看到预览的 params_fingerprint
    fireEvent.click(screen.getByTestId('fc-execute'));
    const rec = await screen.findByTestId('fc-receipt');
    expect(rec.textContent).toContain('ref:fc-9');
    expect(mockExecute).toHaveBeenCalledWith(
      'fc-1', 'sess-1',
      expect.objectContaining({ expected_fingerprint: 'cfp-abc' }),
      expect.anything(),
    );
    // persistence 如实区分（session_ref 不被说成永久保存）
    expect(screen.getByTestId('fc-persistence').textContent).toContain('persistenceSession');

    // 打开结果表
    fireEvent.click(screen.getByTestId('fc-open-result'));
    expect(await screen.findByTestId('fc-result')).toBeTruthy();
    expect(mockGetResult).toHaveBeenCalledWith('sess-1', 'ref:fc-9', { ownerToken: null });

    // 加载到地图（与 prepare-flow 同范式）
    fireEvent.click(screen.getByTestId('fc-load-map'));
    await waitFor(() => {
      expect(mockAddLayer).toHaveBeenCalledWith(expect.objectContaining({ _refId: 'ref:fc-9' }));
      expect(mockFetchRefGeoJSON).toHaveBeenCalledWith('ref:fc-9', 'sess-1', { ownerToken: null });
    });
  });

  it('参数变更使旧预览失效并标记 stale', async () => {
    await renderPanel();
    fillFormula();
    fireEvent.submit(screen.getByTestId('fc-form'));
    await screen.findByTestId('fc-preview-view');
    expect(screen.queryByTestId('fc-preview-view')).toBeTruthy();

    // 任一参数（表达式）变更 → generation 自增 → 清空 preview + stale 提示
    fireEvent.change(screen.getByTestId('fc-expression'), { target: { value: '{人口} * 2' } });
    await waitFor(() => expect(screen.queryByTestId('fc-preview-view')).toBeNull());
    expect(screen.getByTestId('fc-stale')).toBeTruthy();
    // 无有效预览时执行按钮禁用
    expect(screen.getByTestId('fc-execute')).toBeDisabled();
  });

  it('迟到的预览响应按 generation 丢弃（不 setState）', async () => {
    let resolvePreview: (v: unknown) => void = () => {};
    mockPreview.mockImplementation(
      () => new Promise((res) => { resolvePreview = res; }),
    );

    await renderPanel();
    fillFormula();
    fireEvent.submit(screen.getByTestId('fc-form'));
    // 等 createPlan + preview 请求都发出（preview 挂起）
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));
    const genAtRequest = mockPreview.mock.calls[0][2] as number;

    // 参数变更使 generation 前进
    fireEvent.change(screen.getByTestId('fc-expression'), { target: { value: '{人口} * 9' } });
    expect(screen.queryByTestId('fc-preview-view')).toBeNull();

    // 旧响应迟到：应被整体丢弃
    await act(async () => { resolvePreview(makePreview(genAtRequest)); });
    expect(screen.queryByTestId('fc-preview-view')).toBeNull();
  });

  it('编译错误展示 code / detail / position / hint', async () => {
    mockCreatePlan.mockRejectedValue({
      body: { detail: {
        code: 'UNKNOWN_FIELD', detail: 'no such field', position: 3, hint: 'check field name',
      } },
    });
    await renderPanel();
    fillFormula();
    fireEvent.submit(screen.getByTestId('fc-form'));

    const err = await screen.findByTestId('fc-compile-error');
    expect(err.textContent).toContain('UNKNOWN_FIELD');
    expect(err.textContent).toContain('no such field');
    expect(err.textContent).toContain('3');
    expect(err.textContent).toContain('check field name');
    expect(screen.queryByTestId('fc-preview-view')).toBeNull();
  });

  it('键盘可操作：label 关联、aria-label、字段按钮插入、Enter 提交', async () => {
    await renderPanel();

    // 输入框与 label 关联（label 文本 = t('targetName')）
    expect(screen.getByLabelText('targetName').getAttribute('data-testid')).toBe('fc-target-name');
    // 搜索框有 aria-label
    expect(screen.getByTestId('fc-field-search').getAttribute('aria-label')).toBe('fieldSearch');
    // 字段引用是原生 button（可聚焦 / Enter 激活）
    const fieldBtn = screen.getByTestId('fc-field-人口');
    expect(fieldBtn.tagName).toBe('BUTTON');
    expect(fieldBtn.getAttribute('aria-label')).toBe('insertField');
    fireEvent.click(fieldBtn);
    expect((screen.getByTestId('fc-expression') as HTMLTextAreaElement).value).toContain('{人口}');

    // Enter（form submit）触发预览
    fireEvent.change(screen.getByTestId('fc-target-name'), { target: { value: '密度' } });
    fireEvent.submit(screen.getByTestId('fc-form'));
    await waitFor(() => expect(mockCreatePlan).toHaveBeenCalled());
  });

  it('无会话时渲染提示且不发请求', () => {
    render(<FieldComputePanel sessionId={null} />);
    expect(screen.getByTestId('fc-no-session')).toBeTruthy();
    expect(mockListUploads).not.toHaveBeenCalled();
  });
});
