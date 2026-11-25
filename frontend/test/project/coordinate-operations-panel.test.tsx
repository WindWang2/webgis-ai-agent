/**
 * G45 坐标操作面板交互测试（G38 data-join-panel.test 同款模式）。
 * 覆盖：源检查卡（行数/CRS 证据/几何类型）→ 候选对照（不可执行原因 +
 * unknown accuracy 绝不显示 0）→ 执行收据（显示副本不可用独立披露、
 * 主成果 completed）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

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
  describeSource: vi.fn(),
  crsLookup: vi.fn(),
  listOperations: vi.fn(),
  createPlan: vi.fn(),
  updatePlan: vi.fn(),
  lockPlan: vi.fn(),
  preview: vi.fn(),
  execute: vi.fn(),
  inspectRun: vi.fn(),
  listPlans: vi.fn(),
  listRuns: vi.fn(),
  resultPage: vi.fn(),
  cancelRun: vi.fn(),
  displayRef: vi.fn(),
}));
vi.mock('@/lib/api/coordinate-operations', () => ({
  coordOpApi: { client: () => apiMock },
  asCoordOpError: vi.fn((err: unknown) => {
    const e = err as { body?: { detail?: { error_code?: string; reason?: string } }; message?: string };
    const d = e?.body?.detail;
    if (d?.error_code) return { code: d.error_code, message: d.reason ?? '' };
    return { code: 'network_error', message: e?.message ?? String(err) };
  }),
}));

import { CoordinateOperationsPanel } from '@/components/sidebar/coordinate-operations/coordinate-operations-panel';
import { useCoordinateOperationsStore } from '@/lib/hooks/use-coordinate-operations';

const DESCRIBE = {
  kind: 'session_ref', label: 'ref:data-test-1', row_count: 1201,
  content_fingerprint: 'fp', crs: 'EPSG:4326',
  crs_evidence: { origin: 'fc_schema_info_crs', trusted: true },
  crs_description: null, fields: [], geometry: { types: ['Point'] },
};

const LISTING = {  source: { kind: 'session_ref', label: 'x', row_count: 5, crs: 'EPSG:4267', crs_identity: 'EPSG:4267' },
  target: { crs: 'EPSG:4269', identity: 'EPSG:4269', name: 'NAD83' },
  listing: {
    candidates: [
      { candidate_id: 'cand_a', availability: 'available', name: 'Ballpark geographic offset',
        accuracy: null, accuracy_unknown: true, ballpark: true, area_of_use: null,
        grids: [], definition_sha256: 'd1', selectable: true },
      { candidate_id: 'cand_u', availability: 'unavailable', name: 'NAD27 to NAD83 (1)',
        accuracy: 0.15, accuracy_unknown: false, ballpark: false, area_of_use: null,
        grids: [{ short_name: 'us_noaa_conus.tif', available: false, url: 'https://cdn.proj.org/x' }],
        definition_sha256: 'd2', missing_reason: '依赖格网在当前离线环境不可用: us_noaa_conus.tif',
        selectable: false },
    ],
    available_count: 1, unavailable_count: 1,
    available_truncated: 0, unavailable_truncated: 0,
    environment: { pyproj_version: '3.8.0', proj_version: '9.8.1', network_enabled: false, trusted_grid_count: 0 },
    axis_convention: 'always_xy',
  },
};

function renderPanel() {
  useCoordinateOperationsStore.getState().openWith('ref:data-test-1');
  return render(<CoordinateOperationsPanel sessionId="sess-1" ownerToken={null} />);
}

const LOCKED_PLAN = {
  plan_id: 'p1', session_id: 'sess-1', title: 't', status: 'locked',
  revision: 2, plan: { source: { ref: 'ref:data-test-1' }, target_crs: 'EPSG:32650' },
  normalized_digest: 'dg', source_fingerprint: 'fp',
  selected_candidate_id: 'cand_a',
  operation: { candidate_id: 'cand_a', name: 'op', accuracy: null,
    accuracy_unknown: true, ballpark: false, definition_sha256: 'd1',
    environment: {}, projjson_bytes: 100 },
  preview: null, preview_digest: null, preview_stale: false,
  created_at: null, updated_at: null,
};

const COMPLETED_RUN = {
  run_id: 'r1', plan_id: 'p1', status: 'completed', job_id: 1,
  error_code: null, output_ref: 'ref:coord-ops/r1', output_rows: 1201,
  receipt_summary: { rows_total: 1201 }, plan_revision: 2,
  created_at: null, finished_at: null,
};

describe('CoordinateOperationsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.listPlans.mockResolvedValue({ plans: [] });
    apiMock.listRuns.mockResolvedValue({ runs: [] });
    useCoordinateOperationsStore.getState().close();
  });

  it('renders source describe with CRS evidence after click', async () => {
    apiMock.describeSource.mockResolvedValue(DESCRIBE);
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /检查源/ }));
    await waitFor(() => {
      expect(screen.getByText('EPSG:4326')).toBeTruthy();
    });
    expect(screen.getByText(/fc_schema_info_crs/)).toBeTruthy();
    expect(screen.getByText(/Point/)).toBeTruthy();
  });

  it('lists candidates with unavailable reason and unknown accuracy (never 0)', async () => {
    apiMock.describeSource.mockResolvedValue(DESCRIBE);
    apiMock.listOperations.mockResolvedValue(LISTING);
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /检查源/ }));
    await waitFor(() => expect(screen.getByText('EPSG:4326')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /列出候选操作/ }));
    await waitFor(() => {
      expect(screen.getAllByTestId('co-candidate').length).toBe(2);
    });
    expect(screen.getByText(/精度未知/)).toBeTruthy();
    expect(screen.getAllByText(/us_noaa_conus\.tif/).length).toBeGreaterThan(0);
    // 锁定动作可用（每候选一个 + 底部主锁定按钮）
    const lockBtns = screen.getAllByRole('button', { name: /锁定/ });
    expect(lockBtns.length).toBeGreaterThan(0);
  });

  it('restores server plan/run state and shows display unavailable separately', async () => {
    const RUN_DETAIL = {
      run_id: 'r1', plan_id: 'p1', status: 'completed', job_id: 1,
      error_code: null, output_ref: 'ref:coord-ops/r1', output_rows: 1201,
      receipt: {
        run_id: 'r1', rows_transformed: 1201, rows_total: 1201,
        null_geometry: 0, empty_geometry: 0, total_vertices: 1201,
        output_crs: 'EPSG:32650', output_ref: 'ref:coord-ops/r1',
        output_sha256: 's',
        operation: { candidate_id: 'cand_a', name: 'op', accuracy: null,
          accuracy_unknown: true, ballpark: false, definition_sha256: 'd1' },
        display: { state: 'unavailable', reason: 'no op' },
      },
      plan_revision: 2, created_at: null, finished_at: null,
    };
    apiMock.inspectRun.mockResolvedValue(RUN_DETAIL);
    apiMock.listPlans.mockResolvedValue({
      plans: [{
        plan_id: 'p1', session_id: 'sess-1', title: 't', status: 'locked',
        revision: 2, plan: { source: { ref: 'ref:data-test-1' }, target_crs: 'EPSG:32650' },
        normalized_digest: 'dg', source_fingerprint: 'fp',
        selected_candidate_id: 'cand_a',
        operation: { candidate_id: 'cand_a', name: 'op', accuracy: null,
          accuracy_unknown: true, ballpark: false, definition_sha256: 'd1',
          environment: {}, projjson_bytes: 100 },
        preview: null, preview_digest: null, preview_stale: false,
        created_at: null, updated_at: null,
      }],
    });
    apiMock.listRuns.mockResolvedValue({
      runs: [{ run_id: 'r1', plan_id: 'p1', status: 'completed', job_id: 1,
        error_code: null, output_ref: 'ref:coord-ops/r1', output_rows: 1201,
        receipt_summary: { rows_total: 1201 }, plan_revision: 2,
        created_at: null, finished_at: null }],
    });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText('EPSG:32650')).toBeTruthy();
    });
    expect(screen.getByText(/显示副本不可用/)).toBeTruthy();
    expect(screen.getAllByText(/1201/).length).toBeGreaterThan(0);
  });

  it('AOI 缺格不以 0 兜底：只填一格 → 计划不带 aoi、derivation=none', async () => {
    apiMock.describeSource.mockResolvedValue(DESCRIBE);
    apiMock.createPlan.mockResolvedValue({
      plan_id: 'p1', revision: 1, status: 'draft', normalized_digest: 'dg',
      plan: { source: {}, target_crs: 'EPSG:32650' },
    });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /检查源/ }));
    await waitFor(() => expect(screen.getByText('EPSG:4326')).toBeTruthy());
    // 只在 west 输入 116.4（其余三格留空）
    fireEvent.change(screen.getByPlaceholderText('west'), { target: { value: '116.4' } });
    fireEvent.click(screen.getByRole('button', { name: /保存计划/ }));
    await waitFor(() => expect(apiMock.createPlan).toHaveBeenCalled());
    const plan = apiMock.createPlan.mock.calls[0][0] as {
      aoi?: unknown; aoi_derivation?: string;
    };
    expect(plan.aoi_derivation).toBe('none');
    expect(plan.aoi == null).toBe(true);
  });

  it('AOI 清空某格即解除（四格齐全才发 explicit 全量 bbox）', async () => {
    apiMock.describeSource.mockResolvedValue(DESCRIBE);
    apiMock.createPlan.mockResolvedValue({
      plan_id: 'p1', revision: 1, status: 'draft', normalized_digest: 'dg',
      plan: { source: {}, target_crs: 'EPSG:32650' },
    });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /检查源/ }));
    await waitFor(() => expect(screen.getByText('EPSG:4326')).toBeTruthy());
    for (const [k, v] of [['west', '116.4'], ['south', '39.9'], ['east', '116.5'], ['north', '40.0']] as const) {
      fireEvent.change(screen.getByPlaceholderText(k), { target: { value: v } });
    }
    // 清掉 east → 不再是完整 bbox
    fireEvent.change(screen.getByPlaceholderText('east'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /保存计划/ }));
    await waitFor(() => expect(apiMock.createPlan).toHaveBeenCalled());
    const plan = apiMock.createPlan.mock.calls[0][0] as {
      aoi?: unknown; aoi_derivation?: string;
    };
    expect(plan.aoi_derivation).toBe('none');
    expect(plan.aoi == null).toBe(true);
  });

  it('成果只载入一页时披露「已载入 / 总数」（不静默截断）', async () => {
    apiMock.listPlans.mockResolvedValue({ plans: [LOCKED_PLAN] });
    apiMock.listRuns.mockResolvedValue({ runs: [COMPLETED_RUN] });
    apiMock.inspectRun.mockResolvedValue({ ...COMPLETED_RUN, receipt: null });
    const rows = Array.from({ length: 20 }, (_, i) => ({
      properties: { name: `row-${i}` },
      first_coordinate: [i, i],
    }));
    apiMock.resultPage.mockResolvedValue({
      run_id: 'r1', rows, total_rows: 40, cursor: 0, next_cursor: 20,
      page_size: 20, crs: 'EPSG:32650',
    });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: '结果分页' }));
    await waitFor(() => {
      expect(screen.getByTestId('co-result-count')).toHaveTextContent('20 / 40');
    });
  });

  it('轮询超时（非终态）：如实报错而非静默结束', async () => {
    apiMock.listPlans.mockResolvedValue({ plans: [LOCKED_PLAN] });
    apiMock.listRuns.mockResolvedValue({ runs: [] });
    apiMock.execute.mockResolvedValue({ ...COMPLETED_RUN, status: 'running', receipt: null });
    apiMock.inspectRun.mockResolvedValue({ ...COMPLETED_RUN, status: 'running', receipt: null });
    renderPanel();
    const runBtn = await screen.findByRole('button', { name: /执行全量重投影/ });
    // 只对轮询腿用假时钟：600 × 1s 一次推进（真实 1s 间隔测试不可行）。
    vi.useFakeTimers();
    try {
      fireEvent.click(runBtn);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(600_000);
      });
    } finally {
      vi.useRealTimers();
    }
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/poll_timeout/));
    expect(screen.getByRole('button', { name: /执行全量重投影/ })).not.toBeDisabled();
  });
});
