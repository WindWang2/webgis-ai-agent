/**
 * DataJoinPanel 交互测试（G38 WP11）。
 * 覆盖：双源检查卡（行数/CRS/几何披露）→ 键配置 → 预览诊断（全量计数 +
 * blocked typed 码与修正提示如实展示）→ 执行轮询到 completed → 结果分页
 * （total_rows 全量口径）→ 加入地图（hud 层绑定权威渲染 ref）→ blocked
 * 基数诊断不显示绿灯。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

// zustand store 形态：hook 是函数且带 getState（vi.mock 工厂被提升 ——
// mock 形态在工厂内自建，不引用外层变量）
vi.mock('@/components/ui/toast', () => {
  const store = { toasts: [] as unknown[], addToast: vi.fn(), removeToast: vi.fn() };
  return {
    useToastStore: Object.assign(
      (selector: (s: typeof store) => unknown) => selector(store),
      { getState: () => store },
    ),
  };
});

const apiMock = vi.hoisted(() => ({
  describe: vi.fn(),
  createPlan: vi.fn(),
  updatePlan: vi.fn(),
  preview: vi.fn(),
  execute: vi.fn(),
  inspectRun: vi.fn(),
  resultPage: vi.fn(),
  cancelRun: vi.fn(),
  renderRef: vi.fn(),
}));
vi.mock('@/lib/api/data-join-authoring', () => ({
  dataJoinApi: { client: () => apiMock },
  asDataJoinError: vi.fn((err: unknown) => {
    const e = err as { body?: { detail?: { error_code?: string; message?: string; correction_hint?: string } }; message?: string };
    const d = e?.body?.detail;
    if (d?.error_code) return { code: d.error_code, message: d.message ?? '', hint: d.correction_hint };
    return { code: 'network_error', message: e?.message ?? String(err) };
  }),
}));

const addLayer = vi.fn();
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: { getState: () => ({ addLayer }) },
}));

import { DataJoinPanel } from '@/components/sidebar/data-join/data-join-panel';
import { useDataJoinStore } from '@/lib/hooks/use-data-join';

const DESCRIBE_FC = {
  session_id: 's1',
  source: {
    kind: 'session_ref', label: 'ref:fc', is_table: false, row_count: 4,
    content_fingerprint: 'fpL', crs: 'EPSG:4326', fields: [],
    geometry: { types: ['Polygon'] },
  },
  row_count: 4, has_geometry: true,
  sample_fields: [{ name: 'adcode', inferred_type: 'string' }, { name: 'name', inferred_type: 'string' }],
};
const DESCRIBE_TABLE = {
  session_id: 's1',
  source: {
    kind: 'session_ref', label: 'ref:rows', is_table: true, row_count: 4,
    content_fingerprint: 'fpR', crs: null, fields: [], geometry: null,
  },
  row_count: 4, has_geometry: false,
  sample_fields: [{ name: 'adcode', inferred_type: 'string' }, { name: 'pop', inferred_type: 'number' }],
};

const DIAG = {
  basis: 'full', mode: 'attribute', left_rows: 4, right_rows: 4,
  left_null_keys: 0, right_null_keys: 0, left_distinct_keys: 4,
  right_distinct_keys: 4, left_duplicate_keys: 0, right_duplicate_keys: 0,
  candidate_pairs: 3, matched_pairs: 3, matched_left_rows: 3,
  unmatched_left_rows: 1, unmatched_right_rows: 1, left_null_geometry: 0,
  right_invalid_geometry: 0, output_rows: 4, working_crs: null,
  transform_note: null, stage_counts: {}, example_counts: {},
};

/** store 先开再渲染（act 内同步完成；面板 open=false 时返回 null）。 */
function openPanel() {
  useDataJoinStore.getState().openWith();
}

beforeEach(() => {
  vi.clearAllMocks();
  useDataJoinStore.getState().close();
});

describe('DataJoinPanel', () => {
  it('双源检查卡披露行数/CRS/几何形态', async () => {
    apiMock.describe.mockImplementation(async (_src: unknown) => DESCRIBE_FC);
    openPanel();
    render(<DataJoinPanel sessionId="s1" ownerToken={null} />);
    const inputs = screen.getAllByLabelText(/源|source/i);
    fireEvent.change(inputs[0], { target: { value: 'ref:fc' } });
    fireEvent.click(screen.getAllByRole('button', { name: /检查|inspect/i })[0]);
    await waitFor(() => expect(apiMock.describe).toHaveBeenCalled());
    expect(await screen.findByText(/4 (行|rows)/)).toBeInTheDocument();
  });

  it('预览诊断展示全量计数；blocked 如实展示 typed 码与修正提示', async () => {
    apiMock.describe.mockImplementation(async () => DESCRIBE_TABLE);
    apiMock.createPlan.mockResolvedValue({ plan_id: 'p1', revision: 1 });
    apiMock.preview.mockResolvedValue({
      status: 'blocked',
      blocked_code: 'cardinality_violation',
      blocked: {
        error_code: 'cardinality_violation',
        message: '右键重复 1 个（涉及 2 行），与声明 many_to_one 不符',
        correction_hint: '改声明 one_to_many 或声明聚合规则',
      },
      diagnostics: DIAG, duration_ms: 12,
    });
    openPanel();
    render(<DataJoinPanel sessionId="s1" ownerToken={null} />);
    const inputs = screen.getAllByLabelText(/键字段|key field/i);
    fireEvent.change(inputs[0], { target: { value: 'adcode' } });
    fireEvent.change(inputs[1], { target: { value: 'adcode' } });
    fireEvent.click(screen.getByRole('button', { name: /预览诊断|preview/i }));
    const blocked = await screen.findByTestId('dj-blocked');
    expect(blocked).toHaveTextContent('cardinality_violation');
    expect(blocked).toHaveTextContent('右键重复 1 个');
    expect(blocked).toHaveTextContent('改声明');
    // 保存计划被调用（草稿不执行）
    expect(apiMock.createPlan).toHaveBeenCalled();
    expect(apiMock.execute).not.toHaveBeenCalled();
  });

  it('执行轮询到 completed：全量行数 + 分页 + 加入地图（权威渲染 ref）', async () => {
    apiMock.createPlan.mockResolvedValue({ plan_id: 'p1', revision: 1 });
    apiMock.preview.mockResolvedValue({
      status: 'ready', diagnostics: DIAG, sample_rows: [], sample_row_count: 0,
      duration_ms: 5,
    });
    apiMock.execute.mockResolvedValue({
      run_id: 'dj-1', plan_id: 'p1', status: 'running', job_id: 9,
      error_code: null, output_ref: null, output_rows: null, receipt: null,
      created_at: null,
    });
    apiMock.inspectRun.mockResolvedValue({
      run_id: 'dj-1', plan_id: 'p1', status: 'completed', job_id: 9,
      error_code: null, output_ref: 'ref:data-join/dj-1', output_rows: 4,
      receipt: {
        output_kind: 'enriched_layer', output_rows: 4, counts: DIAG,
        working_crs: 'EPSG:4326', duration_ms: 42,
      },
      created_at: null,
    });
    apiMock.resultPage.mockResolvedValue({
      run_id: 'dj-1',
      rows: [{ properties: { adcode: '110101', pop: 708829 }, geometry_summary: { type: 'Polygon' } }],
      total_rows: 4, cursor: 0, next_cursor: 1, page_size: 50,
      crs: 'EPSG:4326', output_kind: 'enriched_layer',
      output_ref: 'ref:data-join/dj-1',
    });
    apiMock.renderRef.mockResolvedValue({ render_ref: 'ref:data-join-render-abc', rows: 4 });
    openPanel();
    render(<DataJoinPanel sessionId="s1" ownerToken={null} />);
    const inputs = screen.getAllByLabelText(/键字段|key field/i);
    fireEvent.change(inputs[0], { target: { value: 'adcode' } });
    fireEvent.change(inputs[1], { target: { value: 'adcode' } });
    fireEvent.click(screen.getByRole('button', { name: /执行|execute/i }));
    // 全量行数口径（total_rows=4，不是当前页 1）
    const totals = await screen.findAllByText(
      /共 4 行|4 rows total/i, {}, { timeout: 5000 });
    expect(totals.length).toBeGreaterThanOrEqual(1);
    // 加入地图：hud 层绑定服务端权威渲染 ref
    fireEvent.click(await screen.findByRole(
      'button', { name: /加入地图|add to map/i }, { timeout: 5000 }));
    await waitFor(() => expect(apiMock.renderRef).toHaveBeenCalledWith('dj-1'));
    await waitFor(() =>
      expect(addLayer).toHaveBeenCalledWith(expect.objectContaining({
        _refId: 'ref:data-join-render-abc',
        type: 'vector',
      })));
  });

  it('F1：空间模式下预览/执行按双源就绪启用（不依赖属性键）', async () => {
    apiMock.createPlan.mockResolvedValue({ plan_id: 'p1', revision: 1 });
    apiMock.preview.mockResolvedValue({
      status: 'ready', diagnostics: DIAG, sample_rows: [], sample_row_count: 0,
      duration_ms: 5,
    });
    openPanel();
    render(<DataJoinPanel sessionId="s1" ownerToken={null} />);
    // 属性模式：无键 → 禁用
    const previewBtn = screen.getByRole('button', { name: /预览诊断|preview/i });
    expect(previewBtn).toBeDisabled();
    // 切到空间模式：仍无源 → 禁用
    fireEvent.change(screen.getByLabelText(/关联模式|join mode/i), {
      target: { value: 'spatial_point_in_polygon' } });
    expect(screen.getByRole('button', { name: /预览诊断|preview/i })).toBeDisabled();
    // 绑定双源 → 启用（不要求键字段）
    const srcInputs = screen.getAllByLabelText(/源|source/i);
    fireEvent.change(srcInputs[0], { target: { value: 'ref:points' } });
    fireEvent.change(srcInputs[1], { target: { value: 'ref:regions' } });
    expect(screen.getByRole('button', { name: /预览诊断|preview/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: /执行|execute/i })).toBeEnabled();
    // pick_by_field 另需择一字段
    fireEvent.change(screen.getByLabelText(/多区域匹配|multi-region/i), {
      target: { value: 'pick_by_field' } });
    expect(screen.getByRole('button', { name: /预览诊断|preview/i })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/择一优先字段|priority field/i), {
      target: { value: 'priority' } });
    expect(screen.getByRole('button', { name: /预览诊断|preview/i })).toBeEnabled();
  });

  it('纯表输出禁用加入地图（诚实披露）', async () => {
    apiMock.createPlan.mockResolvedValue({ plan_id: 'p1', revision: 1 });
    apiMock.execute.mockResolvedValue({
      run_id: 'dj-2', plan_id: 'p1', status: 'completed', job_id: 10,
      error_code: null, output_ref: 'ref:data-join/dj-2', output_rows: 2,
      receipt: null, created_at: null,
    });
    apiMock.resultPage.mockResolvedValue({
      run_id: 'dj-2', rows: [{ properties: { a: 1 }, geometry_summary: null }],
      total_rows: 2, cursor: 0, next_cursor: null, page_size: 50,
      crs: null, output_kind: 'table', output_ref: 'ref:data-join/dj-2',
    });
    openPanel();
    render(<DataJoinPanel sessionId="s1" ownerToken={null} />);
    const inputs = screen.getAllByLabelText(/键字段|key field/i);
    fireEvent.change(inputs[0], { target: { value: 'k' } });
    fireEvent.change(inputs[1], { target: { value: 'k' } });
    fireEvent.click(screen.getByRole('button', { name: /执行|execute/i }));
    const btn = await screen.findByRole('button', { name: /加入地图|add to map/i });
    expect(btn).toBeDisabled();
    expect(apiMock.renderRef).not.toHaveBeenCalled();
  });

  it('关闭面板即停止轮询（不再打 inspectRun）', async () => {
    apiMock.createPlan.mockResolvedValue({ plan_id: 'p1', revision: 1 });
    // 执行后仍 running → 进入 1s 轮询腿
    apiMock.execute.mockResolvedValue({
      run_id: 'dj-3', plan_id: 'p1', status: 'running', job_id: 11,
      error_code: null, output_ref: null, output_rows: null, receipt: null,
      created_at: null,
    });
    openPanel();
    render(<DataJoinPanel sessionId="s1" ownerToken={null} />);
    const inputs = screen.getAllByLabelText(/键字段|key field/i);
    fireEvent.change(inputs[0], { target: { value: 'k' } });
    fireEvent.change(inputs[1], { target: { value: 'k' } });
    fireEvent.click(screen.getByRole('button', { name: /执行|execute/i }));
    await waitFor(() => expect(apiMock.execute).toHaveBeenCalled());
    // 放行 execute 的 promise 续体，让 pollRun 进入首个 1s 休眠
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    // 未到终态即关闭面板
    await act(async () => { useDataJoinStore.getState().close(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 1300)); });
    expect(apiMock.inspectRun).not.toHaveBeenCalled();
  });

  it('会话切换后迟到的 describe 失败不显示在新会话面板', async () => {
    let reject!: (err: unknown) => void;
    apiMock.describe.mockImplementation(
      () => new Promise((_resolve, rej) => { reject = rej; }),
    );
    openPanel();
    const view = render(<DataJoinPanel sessionId="s1" ownerToken={null} />);
    const inputs = screen.getAllByLabelText(/源|source/i);
    fireEvent.change(inputs[0], { target: { value: 'ref:fc' } });
    fireEvent.click(screen.getAllByRole('button', { name: /检查|inspect/i })[0]);
    await waitFor(() => expect(apiMock.describe).toHaveBeenCalled());
    // 请求在飞时切换会话 → 迟到的失败不得写进新会话面板
    view.rerender(<DataJoinPanel sessionId="s2" ownerToken={null} />);
    await act(async () => {
      reject(new Error('late failure'));
      await Promise.resolve();
    });
    expect(screen.queryByTestId('dj-error')).toBeNull();
  });
});
