/**
 * G43（ADR-0295）：turn_context_receipt 消息 reducer 与面板组件测试。
 *
 * 钉住的语义：
 * - 回执按 turn_id 幂等挂载到最近 user 消息；同 turn 更晚事件覆盖摘要；
 * - 已带其它 turn 回执的历史 user 消息不被覆盖（历史事实）；
 * - 面板渲染状态徽标（not_sent/queued/handled/unknown 各自的诚实文案）
 *   与源去向清单（展开时按 session+turn 拉取完整回执）。
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { TurnContextPanel } from '@/components/chat/turn-context-panel';
import { attachTurnContextReceipt, type ChatStreamMessage } from '@/lib/chat/message-reducer';
import type { TurnContextReceiptSummary } from '@/lib/api/turnContext';

const fetchTurnReceipt = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/turnContext', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/turnContext')>(
    '@/lib/api/turnContext');
  return { ...actual, fetchTurnReceipt };
});

function userMsg(id: string, extra: Partial<ChatStreamMessage> = {}): ChatStreamMessage {
  return { id, role: 'user', content: 'hi', timestamp: 1, ...extra };
}

function summary(over: Partial<TurnContextReceiptSummary> = {}): TurnContextReceiptSummary {
  return {
    session_id: 's1',
    turn_id: 'turn-1',
    receipt_id: 7,
    digest: 'abcd1234',
    status: 'submitted',
    disposition: 'started',
    turn_outcome: null,
    policy_version: 2,
    mode: 'typed',
    counts: { included: 5, omitted: 1, truncated: 0, sources_total: 8 },
    persisted: true,
    ...over,
  };
}

describe('attachTurnContextReceipt', () => {
  it('attaches to the latest user message and upgrades same-turn summaries', () => {
    const prev = [userMsg('u1'), { ...userMsg('a1'), role: 'assistant', content: 'ok' } as ChatStreamMessage, userMsg('u2')];
    let next = attachTurnContextReceipt(prev, {
      turnId: 'turn-1', receipt: summary({ status: 'assembled', disposition: null }),
    });
    expect(next[2].turnContextReceipt?.status).toBe('assembled');
    // 同 turn 更晚事件：submitted 覆盖 assembled（按 turn 幂等升级）
    next = attachTurnContextReceipt(next, {
      turnId: 'turn-1', receipt: summary({ status: 'submitted', disposition: 'queued' }),
    });
    expect(next[2].turnContextReceipt?.status).toBe('submitted');
    expect(next[2].turnContextReceipt?.disposition).toBe('queued');
  });

  it('never rewrites an older message already carrying another turn receipt', () => {
    const prev = [
      userMsg('u1', { turnContextReceipt: summary({ turn_id: 'turn-0' }) }),
      userMsg('u2'),
    ];
    // 迟到的 turn-0 事件不得改写 u1 或错误挂到 u2
    const next = attachTurnContextReceipt(prev, {
      turnId: 'turn-0', receipt: summary({ turn_id: 'turn-0', status: 'unknown' }),
    });
    expect(next[0].turnContextReceipt?.status).toBe('submitted'); // 未被弱事件改写
    expect(next[1].turnContextReceipt).toBeUndefined();
  });

  it('drops late events when no mount point exists', () => {
    const prev = [{ ...userMsg('a1'), role: 'assistant', content: 'x' } as ChatStreamMessage];
    const next = attachTurnContextReceipt(prev, {
      turnId: 'turn-9', receipt: summary(),
    });
    expect(next).toBe(prev);
  });
});

describe('TurnContextPanel', () => {
  beforeEach(() => {
    fetchTurnReceipt.mockReset();
  });

  it('renders honest status badges', async () => {
    fetchTurnReceipt.mockResolvedValue({
      session_id: 's1', turn_id: 'turn-1', receipt_id: 7,
      assembly_mode: 'typed', run_id: '', digest: 'd', status: 'submitted',
      disposition: 'queued', turn_outcome: null, policy_version: 2,
      created_at: null, updated_at: null,
      counts: {}, persisted: true, mode: 'typed',
      payload: {
        sources: [
          { source_id: 'pk:1', kind: 'project_knowledge_entry', decision: 'included', version_token: 'tok-1' },
          { source_id: 'cm:2', kind: 'carto_memory_fact', decision: 'excluded_by_policy' },
        ],
      },
    });
    render(
      <TurnContextPanel sessionId="s1" summary={summary({ status: 'submitted', disposition: 'queued' })} />,
    );
    expect(screen.getByTestId('turn-context-receipt-status').dataset.status)
      .toBe('submitted');
    expect(screen.getByTestId('turn-context-receipt-status').dataset.disposition)
      .toBe('queued');
    fireEvent.click(screen.getByTestId('turn-context-receipt-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('turn-context-receipt-sources')).toBeTruthy();
    });
    expect(fetchTurnReceipt).toHaveBeenCalledWith('s1', 'turn-1', undefined);
    const sources = screen.getByTestId('turn-context-receipt-sources').textContent ?? '';
    expect(sources).toContain('pk:1');
    expect(sources).toContain('tok-1');
  });

  it('shows not_sent warning without fabricating a send', () => {
    render(
      <TurnContextPanel sessionId="s1" summary={summary({ status: 'not_sent', disposition: null })} />,
    );
    expect(screen.getByTestId('turn-context-receipt-status').dataset.status)
      .toBe('not_sent');
  });

  it('shows coverage-limited note for legacy rows', () => {
    render(
      <TurnContextPanel
        sessionId="s1"
        summary={summary({ mode: 'legacy', persisted: false })}
      />,
    );
    fireEvent.click(screen.getByTestId('turn-context-receipt-toggle'));
    // 展开即出现诚实 limited 文案（fetch 失败 → missingDetail）
    return waitFor(() => {
      expect(
        screen.getByTestId('turn-context-receipt-missing')
          || screen.getByText(/limited coverage/i),
      ).toBeTruthy();
    });
  });
});
