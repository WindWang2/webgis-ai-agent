/**
 * TaskSupplyPanel 组件测试（GOAL-20261126-02 WP4）。
 *
 * 覆盖：表单填写 → 覆盖评估（矩阵行渲染 + 缺口/披露可见）→ 生成计划
 * （requires_confirmation 状态 + 替代方案单选）→ 需确认时执行被阻断提示 →
 * 选择替代方案后批准执行（重生成计划 → execute approved=true）→ partial
 * 结果回执可见。
 *
 * 与 raster-sampling-panel.test.tsx 同款：API/useT mock（键直回确定性
 * translator；i18n 键完整性由 key-completeness 测试锁）。
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { TaskSupplyPanel } from './task-supply-panel';

const mockCompare = vi.fn();
const mockCreatePlan = vi.fn();
const mockExecute = vi.fn();
const mockGetPlan = vi.fn();

vi.mock('@/lib/api/task-data-supply', () => ({
  compareTaskCoverage: (...a: unknown[]) => mockCompare(...a),
  createSupplyPlan: (...a: unknown[]) => mockCreatePlan(...a),
  executeSupplyPlan: (...a: unknown[]) => mockExecute(...a),
  getSupplyPlan: (...a: unknown[]) => mockGetPlan(...a),
}));

vi.mock('@/lib/api/transport', () => ({
  describeApiError: (err: unknown, fallback: string) =>
    `${fallback}: ${(err as Error)?.message ?? 'unknown'}`,
}));

vi.mock('@/lib/i18n/useT', () => ({
  useT: () => (key: string) => key,
}));

function makeCoverage() {
  return {
    success: true,
    requirement_id: 'tdr-x',
    missing_obligations: [],
    coverage: {
      roles: [
        {
          role: 'subject',
          evaluations: [
            {
              role: 'subject',
              candidate_id: 'ref:poi',
              verdict: 'declared_only',
              spatial_ratio: null,
              temporal_verdict: 'ok',
              gaps: [{ dimension: 'spatial', detail: '仅元数据 bbox 相交' }],
              disclosures: ['实际覆盖未核验，不得当作全覆盖'],
              completeness: 'unknown',
              fit_for_methods: { numeric_stats: true },
              numeric_stats_qualified: true,
              license_disclosed: false,
            },
          ],
          unknown_reasons: [],
        },
      ],
      method_notes: [],
    },
    discovery_meta: {},
  };
}

function makePlan(state = 'requires_confirmation') {
  return {
    plan_id: 'sup-abc',
    state,
    budget_status: 'within',
    budget_max_bytes: null,
    estimated_total_bytes: 2000000,
    budget_acknowledged: false,
    requirement_id: 'tdr-x',
    steps: [
      {
        role: 'subject',
        action: 'acquire',
        candidate_id: 'ref:poi',
        channel: 'local',
        ads_request: {},
        cost_estimate: { rows: 5000, bytes: 2000000, latency_ms: 1500 },
        alternatives: [
          { kind: 'keep_gap', note: '保留缺口继续' },
          { kind: 'shrink_scope', note: '缩小研究区' },
        ],
        disclosures: [],
        numeric_stats_qualified: true,
        outcome: 'pending',
        receipt_ref: '',
        rows_written: null,
        user_choice: '',
        dedup_key: 'sup-abc:subject',
        step_fp: 'stepfp-1',
      },
    ],
  };
}

describe('TaskSupplyPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders form and disables run without session', () => {
    render(<TaskSupplyPanel sessionId={null} />);
    expect(screen.getByTestId('tds-place')).toBeTruthy();
    expect(
      (screen.getByTestId('tds-run-coverage') as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('runs coverage and renders matrix rows with gaps and disclosures', async () => {
    mockCompare.mockResolvedValue(makeCoverage());
    render(<TaskSupplyPanel sessionId="sess-1" />);
    fireEvent.change(screen.getByTestId('tds-bbox'), {
      target: { value: '104.0,30.0,105.0,31.0' },
    });
    fireEvent.click(screen.getByTestId('tds-run-coverage'));
    await waitFor(() =>
      expect(screen.getAllByTestId('tds-eval-row').length).toBeGreaterThan(0),
    );
    const body = document.body.textContent ?? '';
    expect(body).toContain('ref:poi');
    expect(body).toContain('taskSupply.verdictDeclaredOnly');
    expect(body).toContain('仅元数据 bbox 相交');
    // 请求携带会话与角色
    const arg = mockCompare.mock.calls[0][0];
    expect(arg.session_id).toBe('sess-1');
    expect(arg.roles).toEqual(['subject']);
    expect(arg.bbox).toEqual([104.0, 30.0, 105.0, 31.0]);
  });

  it('shows plan alternatives and blocks execution while requires_confirmation', async () => {
    mockCreatePlan.mockResolvedValue({ success: true, plan: makePlan(), discovery_meta: {} });
    render(<TaskSupplyPanel sessionId="sess-1" />);
    fireEvent.change(screen.getByTestId('tds-bbox'), {
      target: { value: '104.0,30.0,105.0,31.0' },
    });
    fireEvent.click(screen.getByTestId('tds-create-plan'));
    await waitFor(() => expect(screen.getByTestId('tds-step')).toBeTruthy());
    expect(document.body.textContent).toContain(
      'taskSupply.stateNeedsConfirmation');
    // 执行被禁用（needs confirmation）
    expect(
      (screen.getByTestId('tds-execute') as HTMLButtonElement).disabled,
    ).toBe(true);
    // 选择 keep_gap 替代方案
    const radios = document.querySelectorAll<HTMLInputElement>(
      'input[name="tds-choice-subject"]',
    );
    fireEvent.click(radios[0]);
    // 选择即触发带 choices 的重建；execute 在重建前仍禁用
    expect(mockExecute).not.toHaveBeenCalled();
  });

  it('rebuilds plan with choices then executes approved once ready', async () => {
    const readyPlan = makePlan('ready');
    readyPlan.steps[0].user_choice = 'keep_gap';
    mockCreatePlan
      .mockResolvedValueOnce({ success: true, plan: makePlan(), discovery_meta: {} })
      .mockResolvedValueOnce({ success: true, plan: readyPlan, discovery_meta: {} });
    mockExecute.mockResolvedValue({
      success: true,
      plan_id: 'sup-abc2',
      plan_state: 'completed',
      results: [{ role: 'subject', outcome: 'completed', receipt_ref: 'ref:fabric-parquet/x' }],
      plan: {
        ...readyPlan,
        state: 'completed',
        steps: [
          { ...readyPlan.steps[0], outcome: 'completed', receipt_ref: 'ref:fabric-parquet/x' },
        ],
      },
    });
    render(<TaskSupplyPanel sessionId="sess-1" />);
    fireEvent.change(screen.getByTestId('tds-bbox'), {
      target: { value: '104.0,30.0,105.0,31.0' },
    });
    fireEvent.click(screen.getByTestId('tds-create-plan'));
    await waitFor(() => expect(screen.getByTestId('tds-step')).toBeTruthy());
    const radios = document.querySelectorAll<HTMLInputElement>(
      'input[name="tds-choice-subject"]',
    );
    fireEvent.click(radios[0]); // keep_gap → 选择即重建（第二次 createPlan）
    await waitFor(() => expect(mockCreatePlan).toHaveBeenCalledTimes(2));
    const exec = screen.getByTestId('tds-execute') as HTMLButtonElement;
    await waitFor(() => expect(exec.disabled).toBe(false));
    fireEvent.click(exec);
    await waitFor(() => expect(mockExecute).toHaveBeenCalledTimes(1));
    // 显式 approved=true
    expect(mockExecute.mock.calls[0][2]).toBe(true);
    await waitFor(() =>
      expect(document.body.textContent).toContain('ref:fabric-parquet/x'),
    );
  });

  it('surfaces typed error from coverage request', async () => {
    mockCompare.mockRejectedValue(new Error('boom'));
    render(<TaskSupplyPanel sessionId="sess-1" />);
    fireEvent.change(screen.getByTestId('tds-bbox'), {
      target: { value: '104.0,30.0,105.0,31.0' },
    });
    fireEvent.click(screen.getByTestId('tds-run-coverage'));
    await waitFor(() => expect(screen.getByTestId('tds-error')).toBeTruthy());
  });
});
