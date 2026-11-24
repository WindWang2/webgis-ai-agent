/**
 * DataFusionPanel 交互测试（多源实体融合工作台）。
 * 覆盖：来源资格卡（ok 态行数/CRS/几何/时间解析披露 + unresolvable 态
 * typed 码与修正提示）→ 预览匹配统计徽章（自动匹配/待审阅/歧义/硬阻断/
 * 未配对）→ 执行 → 审阅队列渲染 + accept 决定携带 expected_revision CAS
 * → commit_blocked 披露 outstanding → stale_review_revision 提示刷新。
 * mock 方式与 data-join-panel.test.tsx 同款：vi.mock API client 模块 +
 * toast store（store 先开再渲染；面板 open=false 时返回 null）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

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
  describeSources: vi.fn(),
  createPlan: vi.fn(),
  listPlans: vi.fn(),
  getPlan: vi.fn(),
  updatePlan: vi.fn(),
  archivePlan: vi.fn(),
  preview: vi.fn(),
  execute: vi.fn(),
  listRuns: vi.fn(),
  inspectRun: vi.fn(),
  resultPage: vi.fn(),
  cancelRun: vi.fn(),
  reviewQueue: vi.fn(),
  listDecisions: vi.fn(),
  upsertDecision: vi.fn(),
  deleteDecision: vi.fn(),
  commitRun: vi.fn(),
  renderRef: vi.fn(),
  refresh: vi.fn(),
}));
vi.mock('@/lib/api/data-fusion-authoring', () => ({
  dataFusionApi: { client: () => apiMock },
  asDataFusionError: vi.fn((err: unknown) => {
    const e = err as {
      body?: { detail?: { error_code?: string; message?: string; correction_hint?: string; detail?: Record<string, unknown> } }
        & { error_code?: string; message?: string; correction_hint?: string; detail?: Record<string, unknown> };
      message?: string;
    };
    const d = e?.body?.detail;
    if (d?.error_code) {
      return { code: d.error_code, message: d.message ?? '', hint: d.correction_hint, detail: d.detail };
    }
    return { code: 'network_error', message: e?.message ?? String(err) };
  }),
}));

import { DataFusionPanel } from '@/components/sidebar/data-fusion/data-fusion-panel';
import { useDataFusionStore } from '@/lib/hooks/use-data-fusion';

// ── fixtures（与 service 返回 dict 投影同构） ──────────────────────

const OK_CARD_A = {
  side: 'a', state: 'ok', adapter_verified: true, label: '来源A', row_count: 320,
  crs: 'EPSG:4326', crs_class: 'geographic', geometry: 'Point',
  invalid_geometry_excluded: 0,
  fields: { name_field: { field: 'name', present: true } },
  temporal_parse: { sample_n: 50, parsed: 48, note: '前 50 行样本；非总体统计' },
  content_fingerprint: 'fpa123', content_revision: null,
  license_declared: null, attribution_declared: null,
};

const UNRESOLVABLE_B = {
  side: 'b', state: 'unresolvable', error_code: 'source_unresolvable',
  message: '源锚解析失败', correction_hint: '确认 ref 存在且当前会话有读取权限',
};

const PLAN_VIEW = {
  plan_id: 'p1', session_id: 's1', title: '', status: 'draft', revision: 1,
  plan: {}, normalized_digest: 'd5', fingerprint_a: null, fingerprint_b: null,
  preview: null, preview_digest: null, created_at: null, updated_at: null,
};

const STATS = {
  rows_a: 20, rows_b: 22, candidates: 40,
  candidate_overflow_entities_a: 0, candidate_overflow_entities_b: 0,
  pairs_matched: 12, pairs_needs_review: 5, pairs_ambiguous: 2,
  unmatched_a: 3, unmatched_b: 4, hard_blocked: 1,
};

const RUN_COMPLETED = {
  run_id: 'df-run-1', plan_id: 'p1', session_id: 's1', status: 'completed',
  job_id: 7, plan_revision: 1, plan_digest: 'd5', base_run_id: null,
  receipt: {
    run_id: 'df-run-1', match_stats: STATS, unmatched_a: 3, unmatched_b: 4,
    fingerprint_a: 'fpa', fingerprint_b: 'fpb', elapsed_s: 1.2,
    state: 'matched_pending_commit',
  },
  error_code: null, output_ref: null, output_sha256: null,
  output_features: null, commit_receipt: null, created_at: null, finished_at: null,
};

const REVIEW_ITEM = {
  pair_key: 'fp:aaaa:bbbb', idx_a: 0, idx_b: 1, status: 'needs_review',
  score: 0.72, hard_block: null, id_pinned: false,
  evidence: [{ kind: 'name', value: 0.7 }, { kind: 'distance', value: 0.9 }],
  renormalized: false,
  geometry_evidence: { distance_m: 42 },
  field_conflicts: { name: 'conflict' },
  decision: null,
};

const REVIEW_QUEUE = {
  run_id: 'df-run-1', total: 1, cursor: 1, has_more: false, items: [REVIEW_ITEM],
};

const EMPTY_DECISIONS = { plan_id: 'p1', decisions: [], cursor: null, has_more: false };

/** typed HTTP 错误（ApiError 形态：body = FastAPI detail）。 */
function httpError(detail: Record<string, unknown>): { body: { detail: Record<string, unknown> }; message: string } {
  return { body: { detail }, message: 'ApiError: 409' };
}

function fillTwoRefs() {
  fireEvent.change(screen.getByLabelText('源A ref'), { target: { value: 'ref:pa' } });
  fireEvent.change(screen.getByLabelText('源B ref'), { target: { value: 'ref:pb' } });
}

/** store 先开再渲染（act 内同步完成；面板 open=false 时返回 null）。 */
function openPanel() {
  useDataFusionStore.getState().openPanel();
}

beforeEach(() => {
  vi.clearAllMocks();
  useDataFusionStore.getState().close();
  apiMock.listPlans.mockResolvedValue({ plans: [], cursor: null, has_more: false });
  apiMock.listDecisions.mockResolvedValue(EMPTY_DECISIONS);
});

describe('DataFusionPanel', () => {
  it('资格卡逐源披露行数/CRS/几何/时间解析；unresolvable 态给 typed 码与修正提示', async () => {
    apiMock.describeSources.mockResolvedValue({
      session_id: 's1', sources: [OK_CARD_A, UNRESOLVABLE_B],
    });
    openPanel();
    render(<DataFusionPanel sessionId="s1" ownerToken={null} />);
    fillTwoRefs();
    fireEvent.click(screen.getByRole('button', { name: '检查来源' }));
    const cardA = await screen.findByTestId('df-card-a');
    expect(cardA).toHaveTextContent('320');
    expect(cardA).toHaveTextContent('EPSG:4326');
    expect(cardA).toHaveTextContent('Point');
    expect(cardA).toHaveTextContent('48/50');
    expect(cardA).toHaveTextContent('name_field');
    // unresolvable 卡如实展示 typed 错误码与 correction_hint
    const unres = screen.getByTestId('df-card-unresolvable');
    expect(unres).toHaveTextContent('source_unresolvable');
    expect(unres).toHaveTextContent('确认 ref 存在');
    // describe body：逐源 side 标记 + 名称字段声明
    expect(apiMock.describeSources).toHaveBeenCalledWith([
      expect.objectContaining({ side: 'a', ref: 'ref:pa', name_field: 'name' }),
      expect.objectContaining({ side: 'b', ref: 'ref:pb', name_field: 'name' }),
    ]);
  });

  it('预览匹配统计徽章（自动匹配/待审阅/歧义/硬阻断/未配对）+ 首次预览先建计划', async () => {
    apiMock.createPlan.mockResolvedValue(PLAN_VIEW);
    apiMock.preview.mockResolvedValue({
      cached: false,
      sources: [OK_CARD_A, OK_CARD_A],
      match_stats: STATS,
      budgets: { max_candidate_pairs: 1000000, max_output_features: 100000 },
      samples: {
        matched: [{ idx_a: 0, idx_b: 1, score: 0.92, name_a: '市第一中学', name_b: '市一中' }],
        review: [{
          idx_a: 2, idx_b: 3, score: 0.66, name_a: '人民医院', name_b: '人民医院（南）',
          status: 'needs_review', field_conflicts: { address: 'conflict' },
        }],
        unmatched_a_first: [], unmatched_b_first: [], sample_limit: 20,
      },
      warnings: [{ item: 'license_undeclared', message: '存在未声明许可/署名的来源：交付时须按既有交付链补齐（G18），本域不代填' }],
    });
    openPanel();
    render(<DataFusionPanel sessionId="s1" ownerToken={null} />);
    fillTwoRefs();
    fireEvent.click(screen.getByRole('button', { name: /预览/ }));
    const badges = await screen.findByTestId('df-match-badges');
    // 五个计数徽章（textContent 顺序拼接 = label+数值）
    expect(badges.textContent).toMatch(/自动匹配12/);
    expect(badges.textContent).toMatch(/待审阅5/);
    expect(badges.textContent).toMatch(/歧义2/);
    expect(badges.textContent).toMatch(/硬阻断1/);
    expect(badges.textContent).toMatch(/未配对7/);
    // 未配对徽章披露 A/B 拆分
    expect(badges.textContent).toMatch(/A 3 · B 4/);
    // 首次预览：先建计划再以 expected_revision=1 预览；绝不顺手执行
    await waitFor(() =>
      expect(apiMock.preview).toHaveBeenCalledWith('p1', 1));
    expect(apiMock.execute).not.toHaveBeenCalled();
  });

  it('执行完成载入审阅队列；accept 决定发送正确 body（含 expected_revision CAS）', async () => {
    apiMock.createPlan.mockResolvedValue(PLAN_VIEW);
    apiMock.execute.mockResolvedValue(RUN_COMPLETED);
    apiMock.reviewQueue.mockResolvedValue(REVIEW_QUEUE);
    apiMock.upsertDecision.mockResolvedValue({
      decision_id: 'd1', plan_id: 'p1', pair_key: 'fp:aaaa:bbbb',
      action: 'accept', revision: 1,
    });
    openPanel();
    render(<DataFusionPanel sessionId="s1" ownerToken={null} />);
    fillTwoRefs();
    fireEvent.click(screen.getByRole('button', { name: /执行/ }));
    const item = await screen.findByTestId('df-review-item');
    // 审阅项：pair_key / 分数 / 状态徽章 / 字段冲突 / 证据分项
    expect(item).toHaveTextContent('fp:aaaa:bbbb');
    expect(item.textContent).toMatch(/0\.720/);
    expect(item).toHaveTextContent('待审阅');
    expect(item).toHaveTextContent('name=冲突');
    expect(item.textContent).toMatch(/name=0\.7/);
    expect(item.textContent).toMatch(/distance=0\.9/);
    // 接受（无人工字段值）→ expected_revision=0（新决定）+ run 锚
    fireEvent.click(screen.getByRole('button', { name: '接受' }));
    await waitFor(() => expect(apiMock.upsertDecision).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({
        pair_key: 'fp:aaaa:bbbb',
        action: 'accept',
        decision_run_id: 'df-run-1',
        idx_a: 0,
        idx_b: 1,
        expected_revision: 0,
      }),
    ));
    // 决定成功 → 队列项就地更新为已决定（后续重提交用新 revision）
    await waitFor(() => expect(item).toHaveTextContent('已决定：接受'));
  });

  it('commit_blocked：require_resolved 政策下如实披露 outstanding 计数与提示', async () => {
    apiMock.createPlan.mockResolvedValue(PLAN_VIEW);
    apiMock.execute.mockResolvedValue(RUN_COMPLETED);
    apiMock.reviewQueue.mockResolvedValue(REVIEW_QUEUE);
    apiMock.commitRun.mockRejectedValue(httpError({
      error_code: 'commit_blocked',
      message: '存在 1 个未决审阅项（政策=require_resolved）：完成审阅后提交，或改为 allow_unresolved',
      correction_hint: '存在未决审阅项：完成审阅后提交，或把计划提交政策改为 allow_unresolved',
      detail: { outstanding: 1 },
    }));
    openPanel();
    render(<DataFusionPanel sessionId="s1" ownerToken={null} />);
    fillTwoRefs();
    fireEvent.click(screen.getByRole('button', { name: /执行/ }));
    await screen.findByTestId('df-review-item');
    fireEvent.click(screen.getByRole('button', { name: '提交' }));
    const blocked = await screen.findByTestId('df-commit-blocked');
    expect(blocked).toHaveTextContent('commit_blocked');
    expect(blocked).toHaveTextContent('1 个未决审阅项');
    // 修正提示给出政策切换路径（允许未决 = allow_unresolved 的面板中文文案）
    expect(blocked).toHaveTextContent('允许未决');
  });

  it('stale_review_revision：CAS 冲突提示刷新队列而非静默覆盖', async () => {
    apiMock.createPlan.mockResolvedValue(PLAN_VIEW);
    apiMock.execute.mockResolvedValue(RUN_COMPLETED);
    apiMock.reviewQueue.mockResolvedValue({
      ...REVIEW_QUEUE,
      items: [{
        ...REVIEW_ITEM,
        decision: {
          decision_id: 'd0', plan_id: 'p1', pair_key: 'fp:aaaa:bbbb',
          action: 'accept', revision: 3,
        },
      }],
    });
    apiMock.upsertDecision.mockRejectedValue(httpError({
      error_code: 'stale_review_revision',
      message: '决定修订冲突：expected 3，当前 4（他人已更新；请刷新后重试）',
      correction_hint: '刷新审阅队列后重试',
    }));
    openPanel();
    render(<DataFusionPanel sessionId="s1" ownerToken={null} />);
    fillTwoRefs();
    fireEvent.click(screen.getByRole('button', { name: /执行/ }));
    await screen.findByTestId('df-review-item');
    // 既有决定 rev3 → CAS expected_revision=3
    fireEvent.click(screen.getByRole('button', { name: '接受' }));
    await waitFor(() => expect(apiMock.upsertDecision).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({ pair_key: 'fp:aaaa:bbbb', action: 'accept', expected_revision: 3 }),
    ));
    const stale = await screen.findByTestId('df-review-stale');
    expect(stale).toHaveTextContent('CAS 冲突');
    expect(stale).toHaveTextContent('刷新队列');
  });
});
