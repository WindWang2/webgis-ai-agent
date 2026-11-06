/**
 * SituationStatusBar / use-situation-projection 测试（ADR-0283 D5）。
 *
 * - 投影 store：成功拉取 → ready + boot 选择重放（仅本地无选择时一次）；
 *   失败 → error（不伪造空态）；
 * - 状态条：六态 chip 按数据面出现/缺席；role=status + aria-live；
 * - 用户现场优先：本地已有选择时不覆盖。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import {
  refreshSituationProjection,
  resetSituationProjection,
} from '@/lib/interaction/use-situation-projection';
import { getSelection, getSelectionIdFilterExpression, resetSelectionStore } from '@/lib/selection/selection-store';
import { SituationStatusBar } from './situation-status-bar';
import { apiFetch } from '@/lib/api/transport';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';

vi.mock('@/lib/api/transport', () => ({
  apiFetch: vi.fn(),
}));
vi.mock('@/lib/mapspec/session-cursor', () => ({
  getMapSpecSessionCursor: vi.fn(() => ({
    sessionId: 'sess-sb-1',
    ownerToken: 'tok',
  })),
  subscribeMapSpecLive: vi.fn(() => () => {}),
  getMapSpecLiveGeneration: vi.fn(() => 0),
}));

const mockedFetch = vi.mocked(apiFetch);

function _situationView(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 'situation_view.v1',
    session_id: 'sess-sb-1',
    situation: {
      interaction: {
        pending_mutations: { status: 'known', value: [{ tool: 'x', status: 'running' }] },
      },
      map: {},
    },
    inconsistencies: [{ code: 'observed_behind_desired', severity: 'info' }],
    interaction_snapshot: {
      selection: {
        layer_id: 'schools',
        selected_ids: ['sch-1', 'sch-2'],
      },
      attempted_not_applied: [],
    },
    selection_history: [],
    claimed_scope: {
      kind: 'feature_ids',
      layer_id: 'schools',
      dataset_ref: 'ref:geojson-aaa',
      feature_ids: ['sch-1', 'sch-2'],
      id_field: 'OBJECTID',
    },
    ...overrides,
  };
}

function _turnChain() {
  return {
    turns: [],
    counts: {
      user_explicit: 1, agent_receipts: 2, agent_pending: 1, executed_observed: 1,
    },
  };
}

beforeEach(() => {
  resetSelectionStore();
  resetSituationProjection();
  mockedFetch.mockReset();
  vi.mocked(getMapSpecSessionCursor).mockReturnValue({ sessionId: "sess-sb-1", ownerToken: "tok", revision: 0, mapspecFingerprint: undefined });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('use-situation-projection store', () => {
  it('fetches both endpoints and replays selection once when local empty', async () => {
    mockedFetch.mockImplementation(async (path: string) => {
      if (path.endsWith('/situation')) return _situationView();
      if (path.endsWith('/turn-chain')) return _turnChain();
      throw new Error('unexpected path ' + path);
    });
    await refreshSituationProjection('sess-sb-1');
    // boot 重放：本地空白 → 服务端 scope 变成 selection
    const selection = getSelection();
    expect(selection?.selected_ids).toEqual(['sch-1', 'sch-2']);
    expect(getSelectionIdFilterExpression('schools')).toEqual([
      'in', ['to-string', ['get', 'OBJECTID']], ['literal', ['sch-1', 'sch-2']],
    ]);
    // 第二次刷新不重复重放（本地已有选择）
    await refreshSituationProjection('sess-sb-1');
    expect(mockedFetch).toHaveBeenCalledTimes(4); // 2 端点 × 2 次
  });

  it('does not replay when local selection exists (user wins)', async () => {
    const { publishSelection } = await import('@/lib/selection/selection-store');
    publishSelection('select', {
      source: 'chart', layer_id: 'districts', selected_categories: ['A'],
    });
    mockedFetch.mockImplementation(async (path: string) =>
      path.endsWith('/situation') ? _situationView() : _turnChain());
    await refreshSituationProjection('sess-sb-1');
    expect(getSelection()?.layer_id).toBe('districts'); // 用户现场保留
  });

  it('records error honestly on fetch failure', async () => {
    mockedFetch.mockRejectedValue(new Error('network down'));
    await refreshSituationProjection('sess-sb-1');
    // React hook 不能在组件外调——经导出的 getter 验证（诚实 error 不伪装空态）。
    const mod = await import('@/lib/interaction/use-situation-projection');
    expect(mod.getSituationProjection().status).toBe('error');
    expect(mod.getSituationProjection().view).toBeNull();
  });
});

describe('SituationStatusBar', () => {
  it('renders six-state chips from server projection', async () => {
    mockedFetch.mockImplementation(async (path: string) =>
      path.endsWith('/situation') ? _situationView() : _turnChain());
    render(<SituationStatusBar />);
    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByTestId('situation-chip-scope')).toBeTruthy();
    });
    expect(screen.getByTestId('situation-chip-selection')).toBeTruthy();
    expect(screen.getByTestId('situation-chip-verifying')).toBeTruthy();
    expect(screen.getByTestId('situation-chip-running')).toBeTruthy();
    expect(screen.getByTestId('situation-chip-retryable')).toBeTruthy();
    expect(screen.getByTestId('situation-chip-provenance')).toBeTruthy();
    const bar = screen.getByTestId('situation-status-bar');
    expect(bar.getAttribute('role')).toBe('status');
    expect(bar.getAttribute('aria-live')).toBe('polite');
  });

  it('renders neutral scope chip when nothing selected', async () => {
    mockedFetch.mockImplementation(async (path: string) =>
      path.endsWith('/situation')
        ? _situationView({
          claimed_scope: { kind: 'none' },
          interaction_snapshot: { selection: null, attempted_not_applied: [] },
          inconsistencies: [],
        })
        : _turnChain());
    render(<SituationStatusBar />);
    await waitFor(() => {
      expect(screen.getByTestId('situation-chip-scope')).toBeTruthy();
    });
    // i18n 消息未在单测装载时回退键名——断言中性态（键回退含 scopeNone）。
    const chip = screen.getByTestId('situation-chip-scope');
    expect(chip.textContent).toMatch(/scopeNone|无分析范围|No analysis scope/);
  });
});


describe('independent review generation guards', () => {
  function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => { resolve = done; });
    return { promise, resolve };
  }

  it('drops a delayed previous session response instead of changing current state or replaying its selection', async () => {
    const { getSituationProjection } = await import('@/lib/interaction/use-situation-projection');
    const a = deferred<ReturnType<typeof _situationView>>();
    const b = deferred<ReturnType<typeof _situationView>>();
    mockedFetch.mockImplementation(async (path) => String(path).endsWith('/turn-chain') ? _turnChain()
      : String(path).includes('session-a') ? a.promise : b.promise);
    vi.mocked(getMapSpecSessionCursor).mockReturnValue({ sessionId: 'session-a', ownerToken: 'a', revision: 0, mapspecFingerprint: undefined });
    const first = refreshSituationProjection('session-a');
    vi.mocked(getMapSpecSessionCursor).mockReturnValue({ sessionId: 'session-b', ownerToken: 'b', revision: 0, mapspecFingerprint: undefined });
    const second = refreshSituationProjection('session-b');
    b.resolve(_situationView({ session_id: 'session-b', claimed_scope: { kind: 'feature_single', layer_id: 'layer-b', feature_ids: ['b'] } }));
    await second;
    a.resolve(_situationView({ session_id: 'session-a', claimed_scope: { kind: 'feature_single', layer_id: 'layer-a', feature_ids: ['a'] } }));
    await first;
    expect(getSituationProjection().sessionId).toBe('session-b');
    expect(getSelection()?.layer_id).toBe('layer-b');
  });

  it('lets the latest same-session request win and invalidates pending completion on reset', async () => {
    const { getSituationProjection } = await import('@/lib/interaction/use-situation-projection');
    const old = deferred<ReturnType<typeof _situationView>>();
    const latest = deferred<ReturnType<typeof _situationView>>();
    let requests = 0;
    mockedFetch.mockImplementation(async (path) => String(path).endsWith('/turn-chain') ? _turnChain() : (++requests === 1 ? old.promise : latest.promise));
    const first = refreshSituationProjection();
    const second = refreshSituationProjection();
    latest.resolve(_situationView({ claimed_scope: { kind: 'feature_single', layer_id: 'new', feature_ids: ['new'] } }));
    await second;
    old.resolve(_situationView());
    await first;
    expect(getSelection()?.layer_id).toBe('new');
    const pending = deferred<ReturnType<typeof _situationView>>();
    mockedFetch.mockImplementation(async () => pending.promise);
    const third = refreshSituationProjection();
    resetSituationProjection();
    pending.resolve(_situationView());
    await third;
    expect(getSituationProjection().status).toBe('idle');
    expect(getSituationProjection().view).toBeNull();
  });

  it('refreshes on durable interaction acknowledgement, coalescing a burst without polling', async () => {
    const { notifySituationInvalidated } = await import('@/lib/interaction/projection-invalidations');
    mockedFetch.mockImplementation(async (path) => String(path).endsWith('/situation') ? _situationView() : _turnChain());
    render(<SituationStatusBar />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(2));
    for (let i = 0; i < 100; i++) notifySituationInvalidated('sess-sb-1');
    await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(4));
    await new Promise((resolve) => setTimeout(resolve, 650));
    expect(mockedFetch).toHaveBeenCalledTimes(4);
  });
});

it('rejects a response from before a live-session reset even if the session id returns to the same value', async () => {
  const cursor = await import('@/lib/mapspec/session-cursor');
  const projection = await import('@/lib/interaction/use-situation-projection');
  let generation = 1;
  vi.mocked(cursor.getMapSpecLiveGeneration).mockImplementation(() => generation);
  let resolve!: (value: ReturnType<typeof _situationView>) => void;
  mockedFetch.mockImplementation(() => new Promise((done) => { resolve = done; }));
  const pending = refreshSituationProjection();
  generation = 3; // A→B→A or resetLiveState, with no newer projection request yet.
  resolve(_situationView());
  await pending;
  expect(projection.getSituationProjection().view).toBeNull();
  expect(getSelection()).toBeNull();
  vi.mocked(cursor.getMapSpecLiveGeneration).mockReturnValue(0);
});
