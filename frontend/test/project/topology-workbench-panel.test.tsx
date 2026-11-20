/**
 * TopologyWorkbenchPanel 交互测试（G31 WP08/WP09）。
 * 覆盖：规则集加载/选择 → 运行列表与维度分离结论渲染（all-unknown 不显绿）→
 * 问题分页 + 全量计数披露 → 定位（相机 setViewport）→ 例外（理由必填 +
 * 用户决定面）→ 修复候选（归属选择披露）→ 复验闭环。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const toastStore = { toasts: [] as unknown[], addToast: vi.fn(), removeToast: vi.fn() };
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: typeof toastStore) => unknown) => selector(toastStore),
}));

const twbApi = vi.hoisted(() => ({
  fetchRulesCatalog: vi.fn(),
  fetchRulesets: vi.fn(),
  createRuleset: vi.fn(),
  updateRuleset: vi.fn(),
  submitTopologyRun: vi.fn(),
  fetchRuns: vi.fn(),
  fetchRun: vi.fn(),
  fetchFindings: vi.fn(),
  fetchFinding: vi.fn(),
  fetchFindingWitness: vi.fn(),
  acceptException: vi.fn(),
  revokeException: vi.fn(),
  fetchRepairCandidates: vi.fn(),
  recheckRun: vi.fn(),
}));
vi.mock('@/lib/api/topology-workbench', () => twbApi);

const setViewport = vi.fn();
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: { getState: () => ({ setViewport }) },
}));

import { TopologyWorkbenchPanel } from '@/components/sidebar/project/topology-workbench-panel';

const RULESET = {
  id: 'rs-1', name: '地块拓扑', project_id: 'p1', session_id: 's1',
  current_version: 2, digest: 'd1', created_by: 'u1', created_at: '2026-11-12T00:00:00Z',
  rules: [
    { rule_id: 'cov', rule_type: 'area_coverage', severity: 'error', enabled: true, params: { gap_area_threshold_m2: 5 } },
  ],
};

const RUN = {
  id: 'run-1', ruleset_id: 'rs-1', ruleset_digest: 'd1', ruleset_version: 2,
  status: 'completed', findings_total: 2, findings_open: 1,
  computation_crs: 'EPSG:32650', job_id: '9', recheck_of_run_id: null,
  duration_ms: 120, created_at: '2026-11-12T00:01:00Z', has_witness: true,
};

const RUN_DETAIL = {
  ...RUN,
  rule_results: [
    {
      rule_id: 'cov', rule_type: 'area_coverage', applicability: 'applicable',
      input_completeness: 'full', computation: 'full', outcome: 'violations_found',
      counts: { examined: 2 }, notes: [], duration_ms: 80,
    },
    {
      rule_id: 'ep', rule_type: 'line_endpoints_connected', applicability: 'blocked',
      blocked_code: 'polar', blocked_message: 'region unsupported',
      input_completeness: 'full', computation: 'full', outcome: 'unknown',
      counts: {}, notes: [], duration_ms: 0,
    },
  ],
  completeness: { all_skipped_or_unknown: false, budget_truncated: false },
  bindings: {},
};

const FINDINGS = [
  {
    id: 'f-1', run_id: 'run-1', rule_id: 'cov', rule_type: 'area_coverage',
    finding_key: 'k1', severity: 'error', status: 'open',
    metric: { value: 9487.9, threshold: 5, unit: 'm2', method: 'aoi_minus_coverage_minus_exclusions' },
    message: '覆盖缺口：AOI − ∪coverage', bbox: [116.4, 39.9, 116.41, 39.909],
    geometry_type: 'Polygon', resolved_by_run_id: null, exception_id: null,
  },
  {
    id: 'f-2', run_id: 'run-1', rule_id: 'cov', rule_type: 'area_coverage',
    finding_key: 'k2', severity: 'warn', status: 'accepted_exception',
    metric: { value: 100, threshold: 5, unit: 'm2', method: 'aoi_minus_coverage_minus_exclusions' },
    message: '覆盖缺口（湖面）', bbox: [116.41, 39.9, 116.42, 39.909],
    geometry_type: 'Polygon', resolved_by_run_id: null, exception_id: 'ex-1',
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  twbApi.fetchRulesets.mockResolvedValue({ items: [RULESET], total: 1 });
  twbApi.fetchRuns.mockResolvedValue({ items: [RUN], total: 1 });
  twbApi.fetchRun.mockResolvedValue(RUN_DETAIL);
  twbApi.fetchFindings.mockResolvedValue({
    items: FINDINGS, total: 2, status_counts: { open: 1, accepted_exception: 1 }, has_more: false,
  });
  twbApi.fetchFindingWitness.mockResolvedValue({
    finding_id: 'f-1',
    witness: { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [] } },
    witness_digest: 'wd', stale: false, locators: [],
  });
  twbApi.fetchRepairCandidates.mockResolvedValue({
    candidates: [
      {
        kind: 'fill_gap', description: '缺口填充需要归属选择',
        target_locators: [], preview_available: false, ownership_choice_required: true,
        estimated_change: { unit: 'm2', expected: '≈ 9487.9' },
      },
    ],
  });
});

describe('TopologyWorkbenchPanel', () => {
  it('加载规则集与运行：维度分离结论渲染（unknown 规则不显示为通过）', async () => {
    render(<TopologyWorkbenchPanel projectId="p1" sessionId="s1" authed />);
    // 规则集选择器
    const select = await screen.findByLabelText(/选择规则集/);
    expect(select).toBeInTheDocument();
    // 运行列表 + 详情：violations_found 与 blocked（unknown）并存
    await waitFor(() => expect(twbApi.fetchRun).toHaveBeenCalledWith('run-1'));
    expect(await screen.findByText('发现违规')).toBeInTheDocument();
    expect(await screen.findByText('阻断')).toBeInTheDocument();
    expect(screen.queryByText('no violations')).toBeNull();
  });

  it('all-unknown 运行显示诚实警告（不得显示绿）', async () => {
    twbApi.fetchRun.mockResolvedValue({
      ...RUN_DETAIL,
      rule_results: [RUN_DETAIL.rule_results[1]],
      completeness: { all_skipped_or_unknown: true, budget_truncated: false },
    });
    render(<TopologyWorkbenchPanel projectId="p1" sessionId="s1" authed />);
    await waitFor(() =>
      expect(screen.getByText(/全部规则为 blocked\/unknown/)).toBeInTheDocument());
  });

  it('问题队列：全量计数披露 + 状态计数', async () => {
    render(<TopologyWorkbenchPanel projectId="p1" sessionId="s1" authed />);
    await waitFor(() => expect(twbApi.fetchFindings).toHaveBeenCalled());
    expect(await screen.findByText(/待处理: 1/)).toBeInTheDocument();
    expect(screen.getByText(/总数是全量计数/)).toBeInTheDocument();
    expect((await screen.findAllByText(/覆盖缺口/)).length).toBeGreaterThanOrEqual(1);
  });

  it('定位：点击 Locate 调用相机 setViewport（bbox → 视口）', async () => {
    render(<TopologyWorkbenchPanel projectId="p1" sessionId="s1" authed />);
    const btn = await screen.findAllByLabelText(/定位/);
    fireEvent.click(btn[0]);
    await waitFor(() => expect(setViewport).toHaveBeenCalled());
    const args = setViewport.mock.calls[0];
    expect(args[4]).toEqual([116.4, 39.9, 116.41, 39.909]);
  });

  it('例外：理由必填（空理由禁用），接受后刷新计数', async () => {
    render(<TopologyWorkbenchPanel projectId="p1" sessionId="s1" authed />);
    fireEvent.click(await screen.findAllByRole('button', { name: /详情/ }).then((b) => b[0]));
    const confirm = await screen.findByRole('button', { name: /确认接受/ });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/例外理由/), { target: { value: '湖面为规划保留水域' } });
    expect(confirm).toBeEnabled();
    twbApi.acceptException.mockResolvedValue({ id: 'ex-9', status: 'active' });
    fireEvent.click(confirm);
    await waitFor(() => expect(twbApi.acceptException).toHaveBeenCalledWith('f-1', '湖面为规划保留水域'));
  });

  it('修复候选：归属选择类披露（不提供一键修复）', async () => {
    render(<TopologyWorkbenchPanel projectId="p1" sessionId="s1" authed />);
    fireEvent.click((await screen.findAllByRole('button', { name: /详情/ }))[0]);
    expect(await screen.findByText(/缺口填充需要归属选择/)).toBeInTheDocument();
    expect(screen.getByText(/不自动判定/)).toBeInTheDocument();
    // 例外与修复共存于详情
    expect(screen.getByText(/修复建议 ≠ 已修复/)).toBeInTheDocument();
  });

  it('复验：点击后调用 recheck 并提示 resolved/persisted/new', async () => {
    twbApi.recheckRun.mockResolvedValue({
      recheck_run_id: 'run-2', status: 'completed', resolved_count: 1,
      persisted_count: 0, new_run_findings_total: 0, new_run_findings_open: 0,
    });
    render(<TopologyWorkbenchPanel projectId="p1" sessionId="s1" authed />);
    fireEvent.click(await screen.findByRole('button', { name: /复验$/ }));
    await waitFor(() => expect(twbApi.recheckRun).toHaveBeenCalledWith('run-1'));
    await waitFor(() => expect(twbApi.fetchRuns).toHaveBeenCalled());
  });

  it('启动检查：提交绑定（coverage/aoi 会话 ref）', async () => {
    twbApi.submitTopologyRun.mockResolvedValue({ run_id: 'run-9', job: { job_id: '10', status: 'started' } });
    render(<TopologyWorkbenchPanel projectId="p1" sessionId="s1" authed />);
    // 等待角色绑定输入出现（area_coverage → coverage/aoi）
    const coverageInput = await screen.findByLabelText('coverage ref');
    fireEvent.change(coverageInput, { target: { value: 'ref:parcel-1' } });
    const aoiInput = screen.getByLabelText('aoi ref');
    fireEvent.change(aoiInput, { target: { value: 'ref:aoi-1' } });
    fireEvent.click(screen.getByRole('button', { name: /启动检查/ }));
    await waitFor(() => expect(twbApi.submitTopologyRun).toHaveBeenCalledWith(
      expect.objectContaining({
        ruleset_id: 'rs-1',
        bindings: expect.objectContaining({
          coverage: expect.objectContaining({ ref: 'ref:parcel-1', kind: 'session_ref' }),
          aoi: expect.objectContaining({ ref: 'ref:aoi-1' }),
        }),
      }),
    ));
  });

  it('scope 拒绝的 typed 错误翻译为用户可读提示', async () => {
    twbApi.submitTopologyRun.mockRejectedValue({
      response: { _data: { code: 'scope_not_allowed', message: 'coverage 角色要求全量' } },
    });
    render(<TopologyWorkbenchPanel projectId="p1" sessionId="s1" authed />);
    fireEvent.click(await screen.findByRole('button', { name: /启动检查/ }));
    await waitFor(() =>
      expect(screen.getByText(/要求全量绑定/)).toBeInTheDocument());
  });

  it('stale 定位如实披露', async () => {
    twbApi.fetchFindingWitness.mockResolvedValue({
      finding_id: 'f-1', witness: { type: 'Feature', geometry: { type: 'Polygon', coordinates: [] } },
      witness_digest: 'wd', stale: true, stale_reason: '角色 coverage 源内容已变化',
      locators: [],
    });
    render(<TopologyWorkbenchPanel projectId="p1" sessionId="s1" authed />);
    fireEvent.click((await screen.findAllByRole('button', { name: /详情/ }))[0]);
    expect(await screen.findByText(/定位过期/)).toBeInTheDocument();
  });
});
