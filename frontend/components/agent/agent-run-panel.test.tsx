/**
 * SW-05：AgentRunPanel 事件驱动刷新测试 —— 情境失效广播（interaction ack /
 * 投影失效）触发 analysis-graph 重拉；非当前会话的失效不触发；防抖合并。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, act } from '@testing-library/react';
import { AgentRunPanel } from './agent-run-panel';
import { notifySituationInvalidated } from '@/lib/interaction/projection-invalidations';
import { setSessionIdentity as setSessionIdentityForTests } from '@/lib/store/session-identity';

function fetchJson(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const GRAPH_BODY = {
  goal: { purpose: '分析学校分布', method_warnings: [] },
  nodes: [
    { id: 'n1', kind: 'analysis', capability: 'poi_query', status: 'complete' },
  ],
  products: [],
  next_action: null,
};

describe('AgentRunPanel situation-invalidation refresh (SW-05)', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    setSessionIdentityForTests('sess-sw5', 'tok-sw5');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('refreshes the analysis graph on same-session invalidation (debounced)', async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue(fetchJson(GRAPH_BODY));
      render(<AgentRunPanel />);
      await act(async () => { await vi.advanceTimersByTimeAsync(50); });
      const before = fetchMock.mock.calls.length;

      // 同会话失效风暴 → 500ms 防抖合并为一次重拉。
      notifySituationInvalidated('sess-sw5');
      notifySituationInvalidated('sess-sw5');
      notifySituationInvalidated('sess-sw5');
      await act(async () => { await vi.advanceTimersByTimeAsync(600); });
      expect(fetchMock.mock.calls.length).toBe(before + 1);

      // 重拉即事件驱动刷新落到 /analysis-graph 投影端点。
      const analysisCalls = fetchMock.mock.calls
        .map((c) => String(c[0]))
        .filter((u) => u.includes('/analysis-graph'));
      expect(analysisCalls.length).toBeGreaterThanOrEqual(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('ignores invalidations from other sessions', async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue(fetchJson(GRAPH_BODY));
      render(<AgentRunPanel />);
      await act(async () => { await vi.advanceTimersByTimeAsync(50); });
      const before = fetchMock.mock.calls.length;

      notifySituationInvalidated('sess-other');
      await act(async () => { await vi.advanceTimersByTimeAsync(700); });
      expect(fetchMock.mock.calls.length).toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });
});
