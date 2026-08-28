import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useSessionPlan } from './use-session-plan';
import type { SessionPlanProjection } from '@/lib/types/session-plan';

// #1048 hydrate-then-delta 状态模型的直接接缝：mount 水合（#1047 行为，
// 刷新仍见当前计划）+ 流式增量应用 + 会话切换清场。渲染规则由
// session-plan-panel.test.tsx 钉，解析由 use-sse-stream.test.ts 钉。
const getSessionPlan = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/chat', () => ({ getSessionPlan }));

function projection(over: Partial<SessionPlanProjection> = {}): SessionPlanProjection {
  return {
    session_id: 's1',
    envelope_id: 'sp-chengdu',
    user_goal: '成都市小学分布情况',
    query: '成都市小学分布情况',
    plan_id: 'plan-chengdu',
    recipe_id: 'poi_distribution_overview',
    progress: [{ capability: 'poi_query', status: 'pending', bound_ref: '' }],
    replaced: false,
    superseded: false,
    updated_at: 1750000000.5,
    ...over,
  };
}

describe('useSessionPlan (#1048 hydrate-then-delta)', () => {
  beforeEach(() => {
    getSessionPlan.mockReset();
  });

  it('hydrates on mount and applies a scripted progress delta onto the projection', async () => {
    getSessionPlan.mockResolvedValue(projection());
    const { result } = renderHook(() => useSessionPlan('s1', 'tok-1'));
    await waitFor(() => expect(result.current.view.plan).not.toBeNull());
    expect(getSessionPlan).toHaveBeenCalledWith('s1', 'tok-1');

    act(() => {
      result.current.applySessionPlanEvent('session_plan_progress', {
        session_id: 's1',
        envelope_id: 'sp-chengdu',
        capability: 'poi_query',
        status: 'complete',
        bound_ref: 'ref:geojson-poi',
      });
    });
    expect(result.current.view.plan?.progress[0]).toMatchObject({
      capability: 'poi_query',
      status: 'complete',
      bound_ref: 'ref:geojson-poi',
    });
  });

  it('re-hydrates and clears state when the session id changes', async () => {
    getSessionPlan.mockResolvedValue(projection());
    const { result, rerender } = renderHook(
      ({ sid }) => useSessionPlan(sid, 'tok-1'),
      { initialProps: { sid: 's1' as string | null } },
    );
    await waitFor(() => expect(getSessionPlan).toHaveBeenCalledTimes(1));

    getSessionPlan.mockResolvedValue(
      projection({ session_id: 's2', user_goal: '分析北京学校', query: '分析北京学校' }),
    );
    rerender({ sid: 's2' });
    await waitFor(() => expect(getSessionPlan).toHaveBeenCalledTimes(2));
    expect(getSessionPlan).toHaveBeenLastCalledWith('s2', 'tok-1');
    await waitFor(() => expect(result.current.view.plan?.session_id).toBe('s2'));
    expect(result.current.view.supersede).toBeNull();
  });

  it('stays empty when hydration fails, and an updated event still opens the envelope', async () => {
    getSessionPlan.mockRejectedValue(new Error('network down'));
    const { result } = renderHook(() => useSessionPlan('s1', null));
    await waitFor(() => expect(getSessionPlan).toHaveBeenCalled());
    expect(result.current.view.plan).toBeNull();

    act(() => {
      result.current.applySessionPlanEvent('session_plan_updated', {
        session_id: 's1',
        envelope_id: 'sp-chengdu',
        plan_id: 'plan-chengdu',
        recipe_id: 'poi_distribution_overview',
        query: '成都市小学分布情况',
        replaced: false,
      });
    });
    expect(result.current.view.plan?.query).toBe('成都市小学分布情况');
    expect(result.current.view.plan?.progress).toEqual([]);
  });

  it('superseded transitions the envelope and carries the goal hand-off banner', async () => {
    getSessionPlan.mockResolvedValue(projection());
    const { result } = renderHook(() => useSessionPlan('s1', 'tok-1'));
    await waitFor(() => expect(result.current.view.plan).not.toBeNull());

    act(() => {
      result.current.applySessionPlanEvent('session_plan_superseded', {
        session_id: 's1',
        old_envelope_id: 'sp-chengdu',
        envelope_id: 'sp-beijing',
        previous_query: '成都市小学分布情况',
        query: '分析北京学校',
      });
    });
    expect(result.current.view.supersede).toEqual({
      previous_goal: '成都市小学分布情况',
      goal: '分析北京学校',
    });
    expect(result.current.view.plan?.envelope_id).toBe('sp-beijing');
    expect(result.current.view.plan?.progress).toEqual([]);
  });

  it('renders nothing without a session and never fetches', () => {
    const { result } = renderHook(() => useSessionPlan(null, null));
    expect(result.current.view.plan).toBeNull();
    expect(getSessionPlan).not.toHaveBeenCalled();
  });
});
