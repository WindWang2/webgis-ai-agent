import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { SessionPlanPanel } from './session-plan-panel';
import type { SessionPlanEventName, SessionPlanProjection } from '@/lib/types/session-plan';
import {
  applySessionPlanEvent,
  EMPTY_SESSION_PLAN_STATE,
  type SessionPlanViewState,
} from '@/lib/session/session-plan-delta';

// 面板测试的接缝是组件本身（spec Testing Decisions #2）：水合结果由 mock 的
// getSessionPlan 控制，钉每一个视觉状态；fetch 契约由 chat.test.ts 钉。
const getSessionPlan = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/chat', () => ({ getSessionPlan }));

const buildProjection = (): SessionPlanProjection => ({
  session_id: 's1',
  envelope_id: 'sp-abc',
  user_goal: '成都市小学分布情况',
  query: '成都市小学分布情况',
  plan_id: 'plan-成都市',
  recipe_id: 'poi_distribution_overview',
  progress: [
    { capability: 'poi_query', status: 'complete', bound_ref: 'ref:geojson-poi' },
    { capability: 'admin_boundary', status: 'pending', bound_ref: '' },
    { capability: 'heatmap', status: 'voided', bound_ref: '' },
    { capability: 'buffer', status: 'unavailable', bound_ref: '' },
  ],
  replaced: false,
  superseded: false,
  updated_at: 1750000000.5,
});

function mockHydration(result: SessionPlanProjection | undefined | Error) {
  getSessionPlan.mockImplementationOnce(() =>
    result instanceof Error ? Promise.reject(result) : Promise.resolve(result)
  );
}

describe('SessionPlanPanel', () => {
  beforeEach(() => {
    getSessionPlan.mockReset();
  });

  it('renders goal, recipe, and capability rows with zh labels', async () => {
    mockHydration(buildProjection());
    render(<SessionPlanPanel sessionId="s1" ownerToken="tok-1" />);
    await waitFor(() =>
      expect(screen.getByText('成都市小学分布情况')).toBeInTheDocument()
    );
    expect(screen.getByText('目标')).toBeInTheDocument();
    expect(screen.getByText('poi_distribution_overview')).toBeInTheDocument();
    expect(screen.getByText('poi_query')).toBeInTheDocument();
    expect(screen.getByText('admin_boundary')).toBeInTheDocument();
  });

  it('renders all four capability statuses with distinct zh labels', async () => {
    mockHydration(buildProjection());
    render(<SessionPlanPanel sessionId="s1" ownerToken="tok-1" />);
    await waitFor(() => expect(screen.getByText('poi_query')).toBeInTheDocument());
    expect(screen.getByText('已完成')).toBeInTheDocument(); // complete
    expect(screen.getByText('待完成')).toBeInTheDocument(); // pending
    expect(screen.getByText('已作废')).toBeInTheDocument(); // voided
    expect(screen.getByText('不可用')).toBeInTheDocument(); // unavailable
  });

  it('shows bound_ref on completed rows only', async () => {
    mockHydration(buildProjection());
    const { container } = render(<SessionPlanPanel sessionId="s1" ownerToken="tok-1" />);
    await waitFor(() => expect(screen.getByText('poi_query')).toBeInTheDocument());
    expect(screen.getByText('ref:geojson-poi')).toBeInTheDocument();
    // 其余行未绑定数据 ref —— bound_ref 空串不渲染。
    expect(container.textContent).not.toContain('admin_boundary ref:');
  });

  it('is entirely hidden when no envelope exists (204)', async () => {
    mockHydration(undefined);
    const { container } = render(<SessionPlanPanel sessionId="s1" ownerToken="tok-1" />);
    await waitFor(() => expect(getSessionPlan).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it('is hidden before hydration and while there is no session', () => {
    const { container } = render(<SessionPlanPanel sessionId={null} ownerToken={null} />);
    expect(container.firstChild).toBeNull();
    expect(getSessionPlan).not.toHaveBeenCalled();
  });

  it('shows the explicit empty state when the slot is open but the chapter is empty', async () => {
    const emptyChapter = {
      ...buildProjection(),
      query: null,
      plan_id: null,
      recipe_id: null,
      progress: [],
      user_goal: '',
    };
    mockHydration(emptyChapter);
    const { container } = render(<SessionPlanPanel sessionId="s1" ownerToken="tok-1" />);
    await waitFor(() => expect(screen.getByText('暂无计划内容')).toBeInTheDocument());
    // 空状态是显式的卡片，不是空白，也不渲染目标/配方/能力行。
    expect(screen.queryByText('目标')).not.toBeInTheDocument();
    expect(container.querySelector('[data-testid="session-plan-empty"]')).not.toBeNull();
  });

  it('re-hydrates when the session id changes (refresh / session switch)', async () => {
    mockHydration(buildProjection());
    const { rerender } = render(<SessionPlanPanel sessionId="s1" ownerToken="tok-1" />);
    await waitFor(() => expect(getSessionPlan).toHaveBeenCalledTimes(1));
    expect(getSessionPlan).toHaveBeenLastCalledWith('s1', 'tok-1');

    mockHydration({
      ...buildProjection(),
      session_id: 's2',
      user_goal: '分析北京学校',
      query: '分析北京学校',
    });
    rerender(<SessionPlanPanel sessionId="s2" ownerToken="tok-1" />);
    await waitFor(() => expect(getSessionPlan).toHaveBeenCalledTimes(2));
    expect(getSessionPlan).toHaveBeenLastCalledWith('s2', 'tok-1');
    await waitFor(() => expect(screen.getByText('分析北京学校')).toBeInTheDocument());
  });

  it('stays hidden when the plan endpoint fails (degraded API never blocks chat)', async () => {
    mockHydration(new Error('network down'));
    const { container } = render(<SessionPlanPanel sessionId="s1" ownerToken="tok-1" />);
    await waitFor(() => expect(getSessionPlan).toHaveBeenCalled());
    await waitFor(() => expect(container.firstChild).toBeNull());
  });
});

// ── #1048: live deltas ───────────────────────────────────────────────────
// 渲染接缝（spec Testing Decisions #2）：scripted SSE 事件经
// applySessionPlanEvent 派生 live 状态，断言面板渲染。fixtures 是冻结的
// 线上契约逐字形状（app/services/session_plan.py 构造）。
describe('SessionPlanPanel live deltas (#1048)', () => {
  beforeEach(() => {
    getSessionPlan.mockReset(); // 上一个 describe 的水合调用不计入本块的断言
  });

  // 字面量 wire fixtures（冻结契约，不得改形）。
  const UPDATED = {
    session_id: 's1',
    envelope_id: 'sp-chengdu',
    plan_id: 'plan-chengdu',
    recipe_id: 'poi_distribution_overview',
    query: '成都市小学分布情况',
    replaced: false,
  };
  const PROGRESS = {
    session_id: 's1',
    envelope_id: 'sp-chengdu',
    capability: 'poi_query',
    status: 'complete',
    bound_ref: 'ref:geojson-poi',
  };
  const SUPERSEDED = {
    session_id: 's1',
    old_envelope_id: 'sp-chengdu',
    envelope_id: 'sp-beijing',
    previous_query: '成都市小学分布情况',
    query: '分析北京学校',
  };

  function apply(
    state: SessionPlanViewState,
    event: SessionPlanEventName,
    data: Record<string, unknown>,
  ) {
    return applySessionPlanEvent(state, event, data);
  }

  it('renders hydrated-shaped live state and skips self-hydration when live is provided', () => {
    const state = apply(EMPTY_SESSION_PLAN_STATE, 'session_plan_updated', UPDATED);
    render(<SessionPlanPanel live={state} />);
    expect(screen.getByText('成都市小学分布情况')).toBeInTheDocument();
    expect(getSessionPlan).not.toHaveBeenCalled(); // 流式状态接管，绝不双重 GET
  });

  it('progress events tick rows live: pending → complete with bound_ref', () => {
    let state = apply(EMPTY_SESSION_PLAN_STATE, 'session_plan_updated', UPDATED);
    state = apply(state, 'session_plan_progress', {
      ...PROGRESS,
      status: 'pending',
      bound_ref: '',
    });
    const { rerender } = render(<SessionPlanPanel live={state} />);
    expect(screen.getByText('poi_query')).toBeInTheDocument();
    expect(screen.getByText('待完成')).toBeInTheDocument();

    state = apply(state, 'session_plan_progress', PROGRESS);
    rerender(<SessionPlanPanel live={state} />);
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getByText('ref:geojson-poi')).toBeInTheDocument();
    expect(screen.queryByText('待完成')).not.toBeInTheDocument();
  });

  it('superseded shows the previous-goal → new-goal banner and drops old rows', () => {
    let state = apply(EMPTY_SESSION_PLAN_STATE, 'session_plan_updated', UPDATED);
    state = apply(state, 'session_plan_progress', PROGRESS);
    state = apply(state, 'session_plan_superseded', SUPERSEDED);
    render(<SessionPlanPanel live={state} />);
    const banner = screen.getByTestId('session-plan-superseded');
    expect(banner.textContent).toContain('成都市小学分布情况');
    expect(banner.textContent).toContain('分析北京学校');
    // 新信封接管：目标已是北京，成都的旧能力行不再出现（user story 9）。
    expect(screen.getByText('分析北京学校')).toBeInTheDocument();
    expect(screen.queryByText('poi_query')).not.toBeInTheDocument();
  });

  it('updated with replaced=true resets rows; replaced=false keeps every row', () => {
    let state = apply(EMPTY_SESSION_PLAN_STATE, 'session_plan_updated', UPDATED);
    state = apply(state, 'session_plan_progress', PROGRESS);
    state = apply(state, 'session_plan_progress', {
      session_id: 's1',
      envelope_id: 'sp-chengdu',
      capability: 'admin_boundary',
      status: 'pending',
      bound_ref: '',
    });
    // replaced=false：目标/配方刷新，全部既有行存活 —— 后端在产品装配后仍发
    // 非 replaced updated，此时 pending 行合法存活，而 progress 只重发变化行
    // （被丢掉的行永不回来），因此任何行都不得在此处被过滤。
    let next = apply(state, 'session_plan_updated', {
      ...UPDATED,
      query: '成都市小学分布（更新）',
      replaced: false,
    });
    const { rerender } = render(<SessionPlanPanel live={next} />);
    expect(screen.getByText('poi_query')).toBeInTheDocument();
    expect(screen.getByText('admin_boundary')).toBeInTheDocument();
    expect(screen.getByText('待完成')).toBeInTheDocument();
    expect(screen.getByText('成都市小学分布（更新）')).toBeInTheDocument();

    // replaced=true：目标更换，旧行全部清空。
    next = apply(state, 'session_plan_updated', { ...UPDATED, replaced: true });
    rerender(<SessionPlanPanel live={next} />);
    expect(screen.queryByText('poi_query')).not.toBeInTheDocument();
    expect(screen.queryByText('admin_boundary')).not.toBeInTheDocument();
  });

  it('drops stale-envelope events: rows and context never merge across envelopes', () => {
    let state = apply(EMPTY_SESSION_PLAN_STATE, 'session_plan_updated', UPDATED);
    state = apply(state, 'session_plan_progress', PROGRESS);
    // 陈旧信封（如 superseded 前的迟到事件）——progress 与 updated 一律丢弃。
    const staleProgress = apply(state, 'session_plan_progress', {
      ...PROGRESS,
      envelope_id: 'sp-stale',
      capability: 'heatmap',
    });
    expect(staleProgress).toBe(state); // 丢弃 = 状态原样（引用判同）
    const staleUpdated = apply(
      state,
      'session_plan_updated',
      { ...UPDATED, envelope_id: 'sp-stale', query: '别涂我' },
    );
    expect(staleUpdated).toBe(state);

    const { rerender } = render(<SessionPlanPanel live={state} />);
    expect(screen.queryByText('heatmap')).not.toBeInTheDocument();
    expect(screen.getByText('成都市小学分布情况')).toBeInTheDocument();
    rerender(<SessionPlanPanel live={staleProgress} />);
    expect(screen.queryByText('heatmap')).not.toBeInTheDocument();
  });

  it('drops a superseded event whose old_envelope_id is not current (same reference)', () => {
    let state = apply(EMPTY_SESSION_PLAN_STATE, 'session_plan_updated', UPDATED);
    state = apply(state, 'session_plan_progress', PROGRESS);
    // 陈旧 superseded（old_envelope_id 与当前信封不符）：整体丢弃 —— 信封
    // 迁移只由匹配的 superseded 驱动，绝不半迁移，状态保持原引用。
    const stale = apply(state, 'session_plan_superseded', {
      ...SUPERSEDED,
      old_envelope_id: 'sp-not-current',
    });
    expect(stale).toBe(state); // 丢弃 = 状态原样（引用判同）
    const { rerender } = render(<SessionPlanPanel live={stale} />);
    expect(screen.queryByTestId('session-plan-superseded')).not.toBeInTheDocument();
    expect(screen.getByText('成都市小学分布情况')).toBeInTheDocument();
    rerender(<SessionPlanPanel live={state} />);
    expect(screen.getByText('poi_query')).toBeInTheDocument();
  });
});
