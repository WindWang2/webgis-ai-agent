/**
 * 方法实验工作台面板交互测试（exploration-panel.test 同款模式：
 * mock @/lib/api/transport 的 apiFetch，组件走真实 client + 真实
 * asMethodExperimentError）。
 *
 * 覆盖：挂载空态 → 检视方法卡 → 编译计划成功（试验清单/预算/预检）→
 * 编译失败（reason_code）→ 运行（counts + trial 状态 + partial 继续）→
 * 比较推荐/弃权两分支 → 采纳成功回执 / 未完成试验 409 拒绝。
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as transport from '@/lib/api/transport';
import { ExperimentPanel } from '@/components/method-experiment/experiment-panel';

vi.mock('@/lib/api/transport', () => ({
  apiFetch: vi.fn(),
}));

const apiFetchMock = vi.mocked(transport.apiFetch);

// ── fixtures（形状与后端路由返回一致） ────────────────────────────────────

const INSPECT = {
  query_matched_families: ['interpolation'],
  resolved_family: 'interpolation',
  family_vocab: [
    'interpolation',
    'density',
    'clustering',
    'network_accessibility',
    'spatial_autocorrelation',
  ],
  cards: [
    {
      family_id: 'interpolation',
      label_zh: '空间插值',
      protocol_kind: 'block_cv',
      default_scheme: 'blocked_kfold',
      default_folds: 5,
      default_metrics: ['cv_rmse', 'cv_mae'],
      requires_value_field: true,
      extra_inputs: [],
      comparability_notes: ['同一折划分下比较'],
      candidates: [
        {
          candidate_id: 'idw',
          method_id: 'interp.idw',
          eval_tool: 'interpolation_block_cv',
          label_zh: '反距离权重 (IDW)',
          sample_floor: 12,
          param_specs: {
            power: {
              role: 'shape', unit: 'unitless', default: 2,
              grid: [1, 2, 3], minimum: 1, maximum: 5, note: '距离权重指数',
            },
          },
          metric_ids: ['cv_rmse', 'cv_mae'],
          limitations: ['对聚类敏感'],
          default_variants: 3,
        },
        {
          candidate_id: 'kriging',
          method_id: 'interp.ordinary_kriging',
          eval_tool: 'interpolation_block_cv',
          label_zh: '普通克里金',
          sample_floor: 20,
          param_specs: {
            variogram_model: {
              role: 'structure', unit: 'unitless', default: 'spherical',
              grid: ['spherical', 'exponential'], minimum: null, maximum: null,
              note: '变差函数模型',
            },
          },
          metric_ids: ['cv_rmse'],
          limitations: ['样本不足时退化'],
          default_variants: 2,
        },
      ],
    },
  ],
  budget_defaults: {
    max_trials: 12,
    max_trials_hard_cap: 24,
    max_candidates: 6,
    max_variants_per_candidate: 8,
    cv_schemes: ['blocked_kfold'],
  },
};

const PLAN = {
  plan: {
    experiment_id: 'mexp-1',
    title: '空间插值',
    family_id: 'interpolation',
    protocol: {
      kind: 'block_cv', family_id: 'interpolation', scheme: 'blocked_kfold',
      folds: 5, stability_runs: 1, max_samples: 5000,
      metrics: ['cv_rmse', 'cv_mae'], notes: [], fingerprint: 'fp123',
    },
    budget: { max_trials: 12, max_features: 0, wall_hint_seconds_per_trial: 2 },
    candidates: [
      {
        candidate_id: 'idw', label_zh: '反距离权重 (IDW)',
        method_id: 'interp.idw', eval_tool: 'interpolation_block_cv',
        variants: 3, param_axes: { power: [1, 2, 3] },
      },
    ],
    trials: [
      { trial_id: 't-001', candidate_id: 'idw', variant_index: 0, params: { power: 1 } },
      { trial_id: 't-002', candidate_id: 'idw', variant_index: 1, params: { power: 2 } },
    ],
    preflight_findings: ['样本量接近下限'],
    fingerprint: 'fp123abc',
  },
};

function runResponse(status: 'partial' | 'completed') {
  return {
    experiment_id: 'mexp-1',
    status,
    counts: { completed: 1, failed: 1, pending: 4 },
    total_wall_ms: 1234,
    trials: [
      {
        trial_id: 't-001', candidate_id: 'idw', state: 'completed',
        metrics: { cv_rmse: 0.21 }, reason_code: '',
      },
      {
        trial_id: 't-002', candidate_id: 'idw', state: 'failed',
        metrics: {}, reason_code: 'MEXP_EVAL_TOOL_MISSING',
      },
    ],
    run_log_tail: [],
  };
}

function compareResponse(mode: 'recommended' | 'abstained') {
  return {
    evaluation: {
      experiment_id: 'mexp-1',
      status: 'partial',
      counts: { completed: 1, failed: 1, pending: 4 },
      total_wall_ms: 1234,
      groups: [
        {
          metric_id: 'cv_rmse',
          direction: 'lower_better',
          unit_kind: 'value_unit',
          n_evaluated: 1,
          n_excluded: 1,
          rows: [
            {
              trial_id: 't-001', candidate_id: 'idw',
              params: { power: 1 }, value: 0.21, basis: { n_folds: 5 },
            },
          ],
          ties: [],
          exclusion_reasons: ['t-002 未完成'],
        },
      ],
      incomparable: ['t-002: 未完成不入组'],
      recommendation: mode === 'recommended'
        ? {
            status: 'recommended',
            abstain_reason: null,
            candidate_id: 'idw',
            trial_id: 't-001',
            metric_evidence: { cv_rmse: 0.21 },
            basis: { n_evaluated: 1 },
            sensitivity: { power: 0.02 },
            alternatives: [{ trial_id: 't-003' }],
            caveats: ['仅 1 个试验完成'],
            pareto_front: ['t-001'],
          }
        : {
            status: 'abstained',
            abstain_reason: '完成的试验不足以推荐',
            candidate_id: null,
            trial_id: null,
            metric_evidence: {},
            basis: {},
            sensitivity: {},
            alternatives: [],
            caveats: [],
            pareto_front: [],
          },
    },
  };
}

const ADOPT = {
  adoption: {
    candidate_id: 'idw',
    trial_id: 't-001',
    metric_snapshot: { cv_rmse: 0.21 },
    reason: 'best cv_rmse',
    received_at: 1737600000,
    plan_fingerprint: 'fp123abc',
  },
};

/** ApiError 同形错误（transport 层抛出；asMethodExperimentError 消费真实实现）。 */
function mexpHttpError(status: number, code: string, message: string): unknown {
  return { status, body: { detail: { reason_code: code, message } } };
}

type RouteMap = Record<string, unknown | (() => unknown)>;
const DEFAULT_ROUTES: RouteMap = {
  '/inspect': INSPECT,
  '/plan': PLAN,
  '/run': () => runResponse('partial'),
  '/compare': () => compareResponse('recommended'),
  '/adopt': ADOPT,
};

function routeApi(map: RouteMap = DEFAULT_ROUTES) {
  apiFetchMock.mockImplementation(async (path: string) => {
    for (const [key, value] of Object.entries(map)) {
      if (path.includes(key)) {
        return (typeof value === 'function' ? (value as () => unknown)() : value) as never;
      }
    }
    throw new Error(`unexpected path: ${String(path)}`);
  });
}

function sentBody(fragment: string, last = false): Record<string, unknown> {
  const matches = apiFetchMock.mock.calls.filter(([p]) => String(p).includes(fragment));
  const call = last ? matches[matches.length - 1] : matches[0];
  if (!call) throw new Error(`no request to ${fragment}`);
  // mock 层收到的是 client 传入的原始 body 对象（真实 transport 才做 stringify）
  return (call[1]?.body ?? {}) as Record<string, unknown>;
}

/** 渲染并推进到「计划已编译」状态（检视 → 填 ref/值字段 → 编译）。 */
async function renderAndCompilePlan(overrides: RouteMap = {}) {
  routeApi({ ...DEFAULT_ROUTES, ...overrides });
  render(<ExperimentPanel sessionId="sess-1" ownerToken={null} />);
  fireEvent.click(await screen.findByTestId('mexp-inspect-btn'));
  await screen.findByTestId('mexp-cards');
  fireEvent.change(screen.getByTestId('mexp-ref-input'), { target: { value: 'ref:stations' } });
  fireEvent.change(screen.getByTestId('mexp-value-field-input'), { target: { value: 'rainfall' } });
  fireEvent.click(screen.getByTestId('mexp-plan-btn'));
  await screen.findByTestId('mexp-plan-summary');
}

beforeEach(() => {
  apiFetchMock.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ExperimentPanel', () => {
  it('挂载渲染空态（empty 态 + 可操作检视按钮）', () => {
    routeApi({});
    render(<ExperimentPanel sessionId="sess-1" ownerToken={null} />);
    expect(screen.getByTestId('mexp-panel')).toBeTruthy();
    expect(screen.getByTestId('mexp-inspect-empty')).toBeTruthy();
    expect(screen.getByTestId('mexp-inspect-btn')).toBeTruthy();
    // 未检视前不发起任何请求
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it('检视渲染方法卡（候选/参数角色/网格/样本下限/局限 + 族解析）', async () => {
    routeApi();
    render(<ExperimentPanel sessionId="sess-1" ownerToken={null} />);
    fireEvent.click(screen.getByTestId('mexp-inspect-btn'));
    await screen.findByTestId('mexp-cards');
    expect(screen.getByTestId('mexp-card-interpolation')).toBeTruthy();
    expect(screen.getByTestId('mexp-resolved-family').textContent).toContain('空间插值');
    const row = screen.getByTestId('mexp-card-candidate-idw');
    expect(row.textContent).toContain('IDW');
    expect(row.textContent).toContain('shape');            // 参数角色 tag
    expect(row.textContent).toContain('[1, 2, 3]');        // 网格值
    expect(row.textContent).toContain('对聚类敏感');        // 局限
    // 请求载荷：POST inspect {query, family_id}
    expect(sentBody('/inspect')).toEqual({ query: '', family_id: '' });
  });

  it('编译计划成功渲染试验清单 + 预算 + 预检；候选可反选', async () => {
    await renderAndCompilePlan();
    expect(screen.getByTestId('mexp-plan-trials').textContent).toContain('t-001');
    expect(screen.getByTestId('mexp-plan-trials').textContent).toContain('power=2');
    expect(screen.getByTestId('mexp-plan-budget').textContent).toContain('12');
    expect(screen.getByTestId('mexp-preflight-0').textContent).toContain('样本量接近下限');
    // 首次全选提交
    expect(sentBody('/plan').candidate_ids).toEqual(['idw', 'kriging']);
    // 反选 kriging 后重编译 → 载荷只带 idw
    routeApi();
    fireEvent.click(screen.getByTestId('mexp-candidate-check-kriging'));
    fireEvent.click(screen.getByTestId('mexp-plan-btn'));
    await screen.findByTestId('mexp-plan-summary');
    expect(sentBody('/plan', true).candidate_ids).toEqual(['idw']);
    expect(sentBody('/plan', true).input_ref).toBe('ref:stations');
    expect(sentBody('/plan', true).value_field).toBe('rainfall');
    expect(sentBody('/plan', true).folds).toBe(5);
    expect(sentBody('/plan', true).max_trials).toBe(12);
  });

  it('编译失败渲染后端 reason_code + message', async () => {
    routeApi({
      '/inspect': INSPECT,
      '/plan': () => { throw mexpHttpError(400, 'MEXP_BUDGET_EXCEEDED', '试验预算超上限'); },
    });
    render(<ExperimentPanel sessionId="sess-1" ownerToken={null} />);
    fireEvent.click(await screen.findByTestId('mexp-inspect-btn'));
    await screen.findByTestId('mexp-cards');
    fireEvent.change(screen.getByTestId('mexp-ref-input'), { target: { value: 'ref:stations' } });
    fireEvent.change(screen.getByTestId('mexp-value-field-input'), { target: { value: 'rainfall' } });
    fireEvent.click(screen.getByTestId('mexp-plan-btn'));
    const err = await screen.findByTestId('mexp-plan-error');
    expect(err.textContent).toContain('MEXP_BUDGET_EXCEEDED');
    expect(err.textContent).toContain('试验预算超上限');
    expect(screen.queryByTestId('mexp-plan-summary')).toBeNull();
  });

  it('运行后渲染 counts 与每 trial 状态；partial 出现继续运行按钮', async () => {
    await renderAndCompilePlan();
    fireEvent.click(screen.getByTestId('mexp-run-btn'));
    await screen.findByTestId('mexp-run-status');
    expect(screen.getByTestId('mexp-run-count-completed').textContent).toContain('1');
    expect(screen.getByTestId('mexp-run-count-failed').textContent).toContain('1');
    expect(screen.getByTestId('mexp-run-trial-t-002').textContent).toContain('failed');
    expect(screen.getByTestId('mexp-run-trial-t-002').textContent).toContain('MEXP_EVAL_TOOL_MISSING');
    expect(screen.getByTestId('mexp-run-wall').textContent).toContain('1234');
    // 请求载荷：窗口 6
    expect(sentBody('/run')).toEqual({ experiment_id: 'mexp-1', max_trials_per_call: 6 });
    // partial → 继续按钮（剩余 pending=4）
    expect(screen.getByTestId('mexp-run-continue-btn').textContent).toContain('4');
  });

  it('比较渲染推荐分支（方向来自服务端 + 证据/敏感性/替代/pareto + 预选试验）', async () => {
    await renderAndCompilePlan();
    fireEvent.click(screen.getByTestId('mexp-compare-btn'));
    await screen.findByTestId('mexp-recommendation');
    expect(screen.getByTestId('mexp-direction-cv_rmse').textContent).toContain('越低越好');
    expect(screen.getByTestId('mexp-recommendation-candidate').textContent).toContain('idw');
    expect(screen.getByTestId('mexp-recommendation-evidence').textContent).toContain('cv_rmse=0.21');
    expect(screen.getByTestId('mexp-recommendation-sensitivity').textContent).toContain('power=0.02');
    expect(screen.getByTestId('mexp-recommendation-alternatives').textContent).toContain('t-003');
    expect(screen.getByTestId('mexp-pareto-front').textContent).toContain('t-001');
    // 推荐试验被预选
    expect(screen.getByTestId('mexp-trial-select-t-001').getAttribute('aria-pressed')).toBe('true');
  });

  it('比较渲染弃权分支（abstain_reason 如实展示，不伪造推荐）', async () => {
    await renderAndCompilePlan({ '/compare': compareResponse('abstained') });
    fireEvent.click(screen.getByTestId('mexp-compare-btn'));
    await screen.findByTestId('mexp-recommendation-abstain');
    expect(screen.getByTestId('mexp-abstain-reason').textContent).toContain('完成的试验不足以推荐');
    expect(screen.queryByTestId('mexp-recommendation-candidate')).toBeNull();
    // 弃权无预选 → 需手选试验
    expect(screen.getByTestId('mexp-trial-select-t-001').getAttribute('aria-pressed')).toBe('false');
  });

  it('采纳成功：POST adopt 载荷正确并渲染回执', async () => {
    await renderAndCompilePlan();
    fireEvent.click(screen.getByTestId('mexp-compare-btn'));
    await screen.findByTestId('mexp-recommendation');
    fireEvent.change(screen.getByTestId('mexp-adopt-reason'), { target: { value: 'best cv_rmse' } });
    fireEvent.click(screen.getByTestId('mexp-adopt-btn'));
    await screen.findByTestId('mexp-adoption-receipt');
    expect(sentBody('/adopt')).toEqual({
      experiment_id: 'mexp-1', trial_id: 't-001', reason: 'best cv_rmse',
    });
    expect(screen.getByTestId('mexp-adoption-line').textContent).toContain('idw');
    expect(screen.getByTestId('mexp-adoption-fingerprint').textContent).toContain('fp123abc');
    expect(screen.getByTestId('mexp-adopt-notice').textContent).toContain('已记录采纳');
    // 记录决定 ≠ 改地图：面板只展示回执提示
    expect(screen.getByTestId('mexp-adopt-selection').textContent).toContain('t-001');
  });

  it('采纳未完成试验：服务端 409 拒绝并如实显示 reason_code', async () => {
    await renderAndCompilePlan({
      '/compare': compareResponse('abstained'),
      '/adopt': () => {
        throw mexpHttpError(409, 'MEXP_TRIAL_NOT_COMPLETED', '采纳目标试验未完成');
      },
    });
    fireEvent.click(screen.getByTestId('mexp-compare-btn'));
    await screen.findByTestId('mexp-recommendation-abstain');
    // 弃权分支无预选 → 手选 t-001（fixture 里该试验未完成）
    fireEvent.click(screen.getByTestId('mexp-trial-select-t-001'));
    fireEvent.click(screen.getByTestId('mexp-adopt-btn'));
    const err = await screen.findByTestId('mexp-adopt-error');
    expect(err.textContent).toContain('MEXP_TRIAL_NOT_COMPLETED');
    expect(screen.queryByTestId('mexp-adoption-receipt')).toBeNull();
  });
});
