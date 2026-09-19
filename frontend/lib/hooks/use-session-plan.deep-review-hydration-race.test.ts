import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useSessionPlan } from './use-session-plan';
import type { SessionPlanProjection } from '@/lib/types/session-plan';

const getSessionPlan = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/chat', () => ({ getSessionPlan }));

function projection(over: Partial<SessionPlanProjection> = {}): SessionPlanProjection {
  return {
    session_id: 's1',
    envelope_id: 'sp-chengdu',
    user_goal: '快照目标',
    query: '快照目标',
    plan_id: 'plan-chengdu',
    recipe_id: 'poi_distribution_overview',
    progress: [],
    replaced: false,
    superseded: false,
    updated_at: 1750000000.5,
    ...over,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const liveUpdated = {
  session_id: 's1',
  envelope_id: 'sp-live',
  plan_id: 'plan-live',
  recipe_id: 'live_recipe',
  query: '流式目标',
  replaced: false,
};

/**
 * FE-06 回归：GET 水合快照会覆盖请求开始之后落账的流式增量；owner_token
 * 补齐（null → issued）也在同一会话内触发清场（面板闪回空态）。
 */
describe('useSessionPlan 水合竞速（FE-06）', () => {
  beforeEach(() => {
    getSessionPlan.mockReset();
  });

  it('请求开始后到达的增量不被随后解析的快照覆盖', async () => {
    const snapshot = deferred<SessionPlanProjection>();
    getSessionPlan.mockReturnValue(snapshot.promise);
    const { result } = renderHook(() => useSessionPlan('s1', 'tok-1'));
    await waitFor(() => expect(getSessionPlan).toHaveBeenCalledTimes(1));

    // 快照仍在飞：增量先落账
    act(() => {
      result.current.applySessionPlanEvent('session_plan_updated', liveUpdated);
    });
    expect(result.current.view.plan?.query).toBe('流式目标');

    await act(async () => {
      snapshot.resolve(projection({ query: '陈旧快照' }));
      await snapshot.promise;
    });

    expect(result.current.view.plan?.query).toBe('流式目标');
    expect(result.current.view.plan?.envelope_id).toBe('sp-live');
  });

  it('owner_token 补齐不清场，且新请求期间的增量同样保留', async () => {
    const first = deferred<SessionPlanProjection>();
    const second = deferred<SessionPlanProjection>();
    getSessionPlan.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    const { result, rerender } = renderHook(
      ({ token }: { token: string | null }) => useSessionPlan('s1', token),
      { initialProps: { token: null as string | null } },
    );
    await waitFor(() => expect(getSessionPlan).toHaveBeenCalledTimes(1));

    act(() => {
      result.current.applySessionPlanEvent('session_plan_updated', liveUpdated);
    });
    expect(result.current.view.plan?.query).toBe('流式目标');

    // 匿名会话拿到 owner_token：同一 session，不得清成 EMPTY
    rerender({ token: 'tok-issued' });
    expect(result.current.view.plan?.query).toBe('流式目标');
    await waitFor(() => expect(getSessionPlan).toHaveBeenCalledTimes(2));
    expect(getSessionPlan).toHaveBeenLastCalledWith('s1', 'tok-issued');

    // 第二个请求开始后又有增量落账；随后解析的旧快照不得覆盖
    act(() => {
      result.current.applySessionPlanEvent('session_plan_progress', {
        session_id: 's1',
        envelope_id: 'sp-live',
        capability: 'poi_query',
        status: 'complete',
        bound_ref: 'ref:geojson-poi',
      });
    });
    await act(async () => {
      second.resolve(projection({ query: '陈旧快照' }));
      await second.promise;
    });

    expect(result.current.view.plan?.query).toBe('流式目标');
    expect(result.current.view.plan?.progress[0]).toMatchObject({
      capability: 'poi_query',
      status: 'complete',
    });
  });

  it('无增量落账时快照照常水合（既有行为不变）', async () => {
    getSessionPlan.mockResolvedValue(projection({ query: '刷新后的计划' }));
    const { result } = renderHook(() => useSessionPlan('s1', 'tok-1'));
    await waitFor(() => expect(result.current.view.plan?.query).toBe('刷新后的计划'));
  });
});
