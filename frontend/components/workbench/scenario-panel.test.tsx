/**
 * G05 WP5 —— ScenarioPanel 用户旅程（组件级）：
 * 基准摘要 → 候选卡（状态徽标/失效原因）→ 分组选择 → 采纳（回执逐项披露）
 * → 锁定释放。空态/加载失败如实呈现（不伪造成功态）。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { resetLiveState, setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const fetchMock = vi.fn();

beforeEach(() => {
  resetLiveState();
  fetchMock.mockReset();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  setMapSpecSessionCursor('g05-fe', 7, 'tok');
});

afterEach(() => {
  cleanup();
  resetLiveState();
});

function overviewPayload() {
  return {
    schema_version: 'scenario_overview.v1',
    overview: {
      total: 2,
      active: 1,
      adopted: 1,
      active_lock_count: 1,
      baseline: {
        baseline_id: 'sb-1',
        mapspec_revision: 7,
        mapspec_fingerprint: 'f'.repeat(64),
        dataset_refs: ['ref:a', 'ref:b'],
        captured_at: '2026-11-25T00:00:00Z',
      },
      candidates: [],
    },
    candidates: [
      {
        candidate_id: 'c-sty',
        title: 'B 方案：分级色带',
        hypothesis: '更均匀的分级',
        branch_session_id: 'g05-fe__wif_scc1',
        baseline_id: 'sb-1',
        fork_revision: 7,
        state: 'comparable',
        delta: [
          { seq: 1, kind: 'PatchLayerStyleIntent', group: 'style', target_id: 'L1', field_paths: ['paint.opacity'], summary: '' },
        ],
        applied_seqs: [1],
        created_by: 'agent',
        adoption_id: '',
        stale_reasons: [],
        compare_digest: '',
      },
      {
        candidate_id: 'c-old',
        title: 'A 方案',
        hypothesis: '',
        branch_session_id: 'g05-fe__wif_scc2',
        baseline_id: 'sb-1',
        fork_revision: 5,
        state: 'stale',
        delta: [],
        applied_seqs: [],
        created_by: 'user',
        adoption_id: '',
        stale_reasons: ['mapspec.revision:5->7'],
        compare_digest: '',
      },
    ],
    active_locks: [
      {
        lock_id: 'lock-1',
        kind: 'layer_field',
        target_id: 'L1',
        field_paths: ['paint.circle-color'],
        reason: '配色定死',
        created_at: '2026-11-25T00:00:00Z',
        value_digest: 'ab',
      },
    ],
  };
}

function adoptionPayload() {
  return {
    schema_version: 'scenario_adoption.v1',
    receipt: {
      adoption_id: 'ad-1',
      session_id: 'g05-fe',
      candidate_id: 'c-sty',
      base_revision: 8,
      final_revision: 9,
      selected_groups: ['style'],
      items: [
        { seq: 1, kind: 'PatchLayerStyleIntent', group: 'style', target_id: 'L1', verdict: 'applied', detail: '' },
        { seq: 2, kind: 'UpsertLayerIntent', group: 'analysis', target_id: 'L9', verdict: 'not_selected', detail: '' },
      ],
      applied_count: 1,
      skipped_count: 1,
      adopted_at: '2026-11-25T00:00:01Z',
      turn_id: '',
      idempotency_key: 'k1',
      pre_adopt_checkpoint_id: 'ck-1',
    },
  };
}

describe('ScenarioPanel', () => {
  it('renders baseline summary, candidate cards, state badges and locks', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes('/scenario/overview')) {
        return Promise.resolve(new Response(JSON.stringify(overviewPayload()), { status: 200 }));
      }
      return Promise.reject(new Error('unexpected ' + url));
    });
    render(<ScenarioPanelUnderTest />);
    expect(await screen.findByTestId('scenario-panel')).toBeTruthy();
    expect(screen.getByTestId('scenario-baseline').textContent).toContain('rev 7');
    const card = await screen.findByTestId('scenario-card-c-sty');
    expect(card.textContent).toContain('B 方案：分级色带');
    expect(screen.getByTestId('scenario-state-c-sty').textContent).toBe('comparable');
    expect(screen.getByTestId('scenario-state-c-old').textContent).toBe('stale');
    // 失效原因如实披露
    expect(screen.getByTestId('scenario-card-c-old').textContent).toContain('mapspec.revision:5->7');
    // 锁定清单与释放按钮
    expect(screen.getByTestId('scenario-locks').textContent).toContain('配色定死');
    expect(screen.getByTestId('scenario-unlock-lock-1')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('group selection switches adopt mode to groups and discloses per-item verdicts', async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).includes('/scenario/overview')) {
        return Promise.resolve(new Response(JSON.stringify(overviewPayload()), { status: 200 }));
      }
      if (String(url).includes('/scenario/candidates/c-sty/adopt')) {
        expect(init?.method).toBe('POST');
        const body = JSON.parse(String(init?.body));
        expect(body.mode).toBe('groups');
        expect(body.groups).toEqual(['style']);
        return Promise.resolve(new Response(JSON.stringify(adoptionPayload()), { status: 200 }));
      }
      return Promise.reject(new Error('unexpected ' + url));
    });
    render(<ScenarioPanelUnderTest />);
    const card = await screen.findByTestId('scenario-card-c-sty');
    fireEvent.click(withinCard(card, '样式'));
    fireEvent.click(screen.getByTestId('scenario-adopt-c-sty'));
    const receipt = await screen.findByTestId('scenario-adoption-receipt');
    expect(receipt.textContent).toContain('(1/2)');
    expect(receipt.textContent).toContain('[applied]');
    expect(receipt.textContent).toContain('[not_selected]');
  });

  it('adopt failure surfaces typed backend error honestly', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes('/scenario/overview')) {
        return Promise.resolve(new Response(JSON.stringify(overviewPayload()), { status: 200 }));
      }
      if (String(url).includes('/adopt')) {
        return Promise.resolve(
          new Response(JSON.stringify({ detail: { error: 'adopt_superseded', message: '父会话并发推进' } }), { status: 409 }),
        );
      }
      return Promise.reject(new Error('unexpected ' + url));
    });
    render(<ScenarioPanelUnderTest />);
    await screen.findByTestId('scenario-card-c-sty');
    fireEvent.click(screen.getByTestId('scenario-adopt-c-sty'));
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('父会话并发推进');
    });
  });

  it('no-session empty state does not fetch', () => {
    setMapSpecSessionCursor(undefined);
    render(<ScenarioPanelUnderTest />);
    expect(screen.getByTestId('scenario-empty')).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

import { ScenarioPanel } from './scenario-panel';

function withinCard(card: Element, text: string): HTMLButtonElement {
  const buttons = Array.from(card.querySelectorAll('button'));
  const hit = buttons.find((b) => b.textContent?.trim() === text);
  if (!hit) throw new Error(`button ${text} not found in card`);
  return hit;
}

function ScenarioPanelUnderTest() {
  return <ScenarioPanel />;
}
