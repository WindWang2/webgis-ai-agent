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
import { getSelection, resetSelectionStore } from '@/lib/selection/selection-store';
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
