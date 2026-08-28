import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { SessionPlanPanel } from './session-plan-panel';
import type { SessionPlanProjection } from '@/lib/types/session-plan';

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
