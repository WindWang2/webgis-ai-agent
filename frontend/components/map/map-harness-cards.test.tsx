/**
 * MapHarnessCards 组件测试（ADR-0278 M6）：回执卡渲染（含 user-wins /
 * superseded / 等待确认态）、owner 批准流、显示确认卡（auto 不显示 /
 * required pending 显示 / ack 成功翻转 / 旧代次拒绝如实报错）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { MapHarnessCards } from './map-harness-cards';
import {
  pushOperationReceipt,
  setReceiptBusSession,
} from '@/lib/map-operations/receipt-bus';
import { clearPendingRemoved, markPendingRemoved } from '@/lib/mapspec/session-cursor';

function fetchJson(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const COMMITTED_RECEIPT = {
  operation_id: 'mop-card-1',
  status: 'committed',
  message: '地图操作已提交：2 项生效，1 项保留用户决策',
  final_revision: 9,
  applied_count: 2,
  skipped_count: 1,
  effects: [
    { index: 0, kind: 'set_layer_visibility', targets: ['heatmap'], state: 'applied' },
    { index: 1, kind: 'set_layer_opacity', targets: ['schools'], state: 'skipped',
      reason_code: 'user_wins' },
    { index: 2, kind: 'set_layer_symbol', targets: ['districts'], state: 'applied' },
  ],
};

describe('MapHarnessCards', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    setReceiptBusSession(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders nothing without a session or receipts', () => {
    const { container } = render(<MapHarnessCards sessionId={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders a committed receipt with per-effect states incl. user-wins', async () => {
    // auto 模式：确认面水合延迟 5s（不抢观察窗口），本用例窗口内不 fetch；
    // 回执卡由 bus 即时驱动，与确认面无关。
    fetchMock.mockResolvedValue(fetchJson({
      session_id: 's1', mode: 'auto', confirmed: true, pending: false,
      required_render_seq: 0, latest_observation_seq: 0, verdict_present: true,
    }));
    render(<MapHarnessCards sessionId="s1" />);
    pushOperationReceipt(COMMITTED_RECEIPT as never, 's1');
    const card = await screen.findByTestId('map-operation-card-mop-card-1');
    expect(card).toBeInTheDocument();
    expect(screen.getByTestId('map-op-status-mop-card-1').textContent).toContain('已提交');
    expect(screen.getByTestId('map-operation-card-mop-card-1').textContent).toContain(
      '保留用户决策',
    );
    // auto → 确认卡不出现
    expect(screen.queryByTestId('display-confirmation-card')).toBeNull();
  });

  it('offers approval for confirmation_required and backfills the final receipt', async () => {
    fetchMock.mockResolvedValue(fetchJson({
      session_id: 's1', mode: 'auto', confirmed: true, pending: false,
      required_render_seq: 0, latest_observation_seq: 0, verdict_present: false,
    }));
    render(<MapHarnessCards sessionId="s1" />);
    pushOperationReceipt({
      operation_id: 'mop-card-2',
      status: 'confirmation_required',
      message: '操作含不可逆效果（移除图层）',
      effects: [{ index: 0, kind: 'remove_layer', targets: ['heatmap'], state: 'planned' }],
    } as never, 's1');
    const approve = await screen.findByTestId('map-op-approve-mop-card-2');
    expect(screen.getByTestId('map-op-status-mop-card-2').textContent).toContain('等待确认');

    // 批准：POST commit → 服务端最终回执回填
    fetchMock.mockResolvedValue(fetchJson({
      receipt: {
        operation_id: 'mop-card-2',
        status: 'committed',
        final_revision: 11,
        effects: [{ index: 0, kind: 'remove_layer', targets: ['heatmap'], state: 'applied' }],
      },
    }));
    fireEvent.click(approve);
    await waitFor(() =>
      expect(screen.getByTestId('map-op-status-mop-card-2').textContent).toContain('已提交'),
    );
    const calls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(calls.some((u) => u.includes('/map-operations/mop-card-2/commit'))).toBe(true);
  });

  it('shows the display confirmation card only when required+pending, and acks at the latest generation', async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue(fetchJson({
        session_id: 's1', mode: 'required', confirmed: false, pending: true,
        required_render_seq: 5, latest_observation_seq: 7, verdict_present: true,
      }));
      render(<MapHarnessCards sessionId="s1" />);
      // 挂载 5s 兜底水合（不抢观察窗口）
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      expect(screen.getByTestId('display-confirmation-card')).toBeInTheDocument();

      // ack 成功（用最新观察代次 7）
      fetchMock.mockResolvedValue(fetchJson({
        accepted: true, confirmed: true, render_seq: 7, task_complete: true,
      }));
      fireEvent.click(screen.getByTestId('display-confirm-ack'));
      await act(async () => { await vi.advanceTimersByTimeAsync(50); });
      expect(screen.queryByTestId('display-confirmation-card')).toBeNull();
      const ackCall = fetchMock.mock.calls
        .map((c) => [String(c[0]), c[1]])
        .find(([u]) => u.includes('/display-confirmation/ack'));
      expect(ackCall).toBeTruthy();
      expect((ackCall![1] as RequestInit).body).toContain('"render_seq":7');
    } finally {
      vi.useRealTimers();
    }
  });

  it('reports stale-ack rejection honestly (no fake success)', async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue(fetchJson({
        session_id: 's1', mode: 'required', confirmed: false, pending: true,
        required_render_seq: 5, latest_observation_seq: 9, verdict_present: true,
      }));
      render(<MapHarnessCards sessionId="s1" />);
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      expect(screen.getByTestId('display-confirmation-card')).toBeInTheDocument();

      fetchMock.mockResolvedValue(fetchJson({
        detail: { error: 'stale_ack', message: '确认代次与当前最新观察代次不一致' },
      }, 409));
      fireEvent.click(screen.getByTestId('display-confirm-ack'));
      await act(async () => { await vi.advanceTimersByTimeAsync(50); });
      const error = screen.getByTestId('display-confirm-error');
      expect(error.textContent).toBeTruthy();
      // 卡不消失 —— pending 事实未变
      expect(screen.getByTestId('display-confirmation-card')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  // ── SW-09：终验收口披露行（auto 不再静默；四层终态诚实分层）───────

  it('discloses auto mode honestly via the passive acceptance line (SW-09)', async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue(fetchJson({
        session_id: 's1', mode: 'auto', confirmed: true, pending: false,
        required_render_seq: 0, latest_observation_seq: 12, verdict_present: true,
      }));
      render(<MapHarnessCards sessionId="s1" />);
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      const line = screen.getByTestId('final-acceptance-line');
      // 「自动确认」明确披露 —— 渲染已发生绝不冒充用户已认可。
      expect(line.textContent).toContain('自动确认');
      expect(line.textContent).toContain('12');
      expect(screen.queryByTestId('display-confirmation-card')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows user-confirmed state on the acceptance line after required-mode ack', async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue(fetchJson({
        session_id: 's1', mode: 'required', confirmed: true, pending: false,
        required_render_seq: 7, latest_observation_seq: 7, verdict_present: true,
      }));
      render(<MapHarnessCards sessionId="s1" />);
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      const line = screen.getByTestId('final-acceptance-line');
      expect(line.textContent).toContain('用户已确认');
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not fabricate a verdict when none exists (SW-09 honesty)', async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue(fetchJson({
        session_id: 's1', mode: 'auto', confirmed: true, pending: false,
        required_render_seq: 0, latest_observation_seq: 3, verdict_present: false,
      }));
      render(<MapHarnessCards sessionId="s1" />);
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      const line = screen.getByTestId('final-acceptance-line');
      expect(line.textContent).toContain('暂无裁决');
      // 无裁决不冒充"已确认"。
      expect(line.textContent).not.toContain('自动确认');
    } finally {
      vi.useRealTimers();
    }
  });

  it('discloses awaiting-user state under required+pending (review P1-1)', async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue(fetchJson({
        session_id: 's1', mode: 'required', confirmed: false, pending: true,
        required_render_seq: 7, latest_observation_seq: 7, verdict_present: true,
      }));
      render(<MapHarnessCards sessionId="s1" />);
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      // 交互卡（动作）与披露行（状态）同屏：行必须说"等待用户确认"，
      // 绝不能显示"自动确认"（required 模式下从未自动确认过）。
      expect(screen.getByTestId('display-confirmation-card')).toBeInTheDocument();
      const line = screen.getByTestId('final-acceptance-line');
      expect(line.textContent).toContain('等待用户确认');
      expect(line.textContent).not.toContain('自动确认');
    } finally {
      vi.useRealTimers();
    }
  });

  it('「稍后」dismiss 后新代次 pending 复现：卡片回归（确认不被静默跳过）', async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockResolvedValue(fetchJson({
        session_id: 's1', mode: 'required', confirmed: false, pending: true,
        required_render_seq: 5, latest_observation_seq: 7, verdict_present: true,
      }));
      render(<MapHarnessCards sessionId="s1" />);
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      expect(screen.getByTestId('display-confirmation-card')).toBeInTheDocument();

      // 「稍后」：本次裁决的卡收起的（done=true）。
      fireEvent.click(screen.getByTestId('display-confirm-dismiss'));
      await act(async () => { await vi.advanceTimersByTimeAsync(50); });
      expect(screen.queryByTestId('display-confirmation-card')).toBeNull();

      // live generation 前进 → 2s debounce 复查：新观测代次仍 pending。
      // 修复前：done 无代次归属，卡永居 null —— required 模式用户确认被静默跳过。
      fetchMock.mockResolvedValue(fetchJson({
        session_id: 's1', mode: 'required', confirmed: false, pending: true,
        required_render_seq: 8, latest_observation_seq: 9, verdict_present: true,
      }));
      markPendingRemoved('layer-a');
      await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
      expect(screen.getByTestId('display-confirmation-card')).toBeInTheDocument();
    } finally {
      clearPendingRemoved();
      vi.useRealTimers();
    }
  });

  it('并发水合乱序返回：陈旧响应不覆盖新态（required pending 不被吞成已确认）', async () => {
    vi.useFakeTimers();
    try {
      // 受控 fetch：按发出顺序挂起，由测试决定响应次序（模拟慢响应后到）。
      const inflight: Array<(r: Response) => void> = [];
      fetchMock.mockImplementation(
        () => new Promise<Response>((resolve) => { inflight.push(resolve); }),
      );
      render(<MapHarnessCards sessionId="s1" />);
      // 挂载 5s 兜底水合 + 回执环回填（两个请求挂起，先不响应）。
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      const displayCalls = () => fetchMock.mock.calls
        .map((c) => String(c[0]))
        .filter((u) => u.includes('/display-confirmation') && !u.includes('/ack'));
      expect(displayCalls()).toHaveLength(1);
      await act(async () => { inflight[1](fetchJson({ operations: [] })); });

      // MapSpec live generation 变化 → 2s debounce 复查（第二次水合发出）。
      markPendingRemoved('layer-a');
      await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
      expect(displayCalls()).toHaveLength(2);

      // 新态先到：required + pending（等待用户确认）。
      await act(async () => {
        inflight[2](fetchJson({
          session_id: 's1', mode: 'required', confirmed: false, pending: true,
          required_render_seq: 9, latest_observation_seq: 9, verdict_present: true,
        }));
      });
      expect(screen.getByTestId('display-confirmation-card')).toBeInTheDocument();
      expect(screen.getByTestId('final-acceptance-line').textContent).toContain('等待用户确认');

      // 旧响应后到（挂载兜底那次的陈旧快照 confirmed:true）—— 修复前它会
      // setConfirmStatus(旧态)：待确认卡被吞、行改口"用户已确认"。
      await act(async () => {
        inflight[0](fetchJson({
          session_id: 's1', mode: 'required', confirmed: true, pending: false,
          required_render_seq: 5, latest_observation_seq: 5, verdict_present: true,
        }));
      });
      expect(screen.getByTestId('display-confirmation-card')).toBeInTheDocument();
      const line = screen.getByTestId('final-acceptance-line');
      expect(line.textContent).toContain('等待用户确认');
      expect(line.textContent).not.toContain('用户已确认');
    } finally {
      clearPendingRemoved();
      vi.useRealTimers();
    }
  });
});
