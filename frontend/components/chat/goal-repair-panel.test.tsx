/**
 * GoalRepairPanel 回归：attempt 载荷非法时必须显式报错，不得静默空转。
 *
 * - start 成功但 parse → null：busy 复位前给出错误（否则无反馈可重复启动）；
 * - 轮询 parse → null：停止轮询并报错（否则永远显示「运行中」每 2s 空转）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { GoalRepairPanel } from './goal-repair-panel';
import {
  cancelGoalRepairAttempt,
  fetchGoalRepairAttempt,
  fetchGoalRepairPreview,
  startGoalRepairAttempt,
} from '@/lib/api/goal-repair';

vi.mock('@/lib/api/goal-repair', () => ({
  fetchGoalRepairAttempt: vi.fn(),
  fetchGoalRepairPreview: vi.fn(),
  startGoalRepairAttempt: vi.fn(),
  cancelGoalRepairAttempt: vi.fn(),
}));

const previewPayload = {
  goal_id: 'goal-1',
  plan_version: 'v1',
  actions: [
    {
      action_id: 'a1',
      action: 'reobserve',
      requirement_id: 'map',
      disposition: 'executable',
      side_effect: 'read_only',
    },
  ],
};

describe('GoalRepairPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(cancelGoalRepairAttempt).mockResolvedValue({});
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('start 载荷解析失败 → 显式错误，不静默复位 busy', async () => {
    vi.mocked(fetchGoalRepairPreview).mockResolvedValue(previewPayload);
    vi.mocked(startGoalRepairAttempt).mockResolvedValue({ not_an_attempt: true });

    render(<GoalRepairPanel sessionId="s1" active />);
    await waitFor(() => {
      expect(screen.getByTestId('goal-repair-start')).not.toBeDisabled();
    });

    fireEvent.click(screen.getByTestId('goal-repair-start'));

    await waitFor(() => {
      expect(screen.getByTestId('goal-repair-error')).toBeTruthy();
    });
    expect(screen.getByTestId('goal-repair-error').textContent).toContain(
      '补全操作失败',
    );
  });

  it('轮询载荷解析失败 → 报错并停止轮询', async () => {
    vi.useFakeTimers();
    vi.mocked(fetchGoalRepairPreview).mockResolvedValue({
      ...previewPayload,
      active_attempt: {
        attempt_id: 'at1',
        status: 'running',
        active: true,
        cancellable: true,
        actions: [],
      },
    });
    vi.mocked(fetchGoalRepairAttempt).mockResolvedValue({ not_an_attempt: true });

    render(<GoalRepairPanel sessionId="s1" active />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId('goal-repair-panel')).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByTestId('goal-repair-error')).toBeTruthy();
    expect(vi.mocked(fetchGoalRepairAttempt)).toHaveBeenCalledTimes(1);

    // 停止轮询：再过一个周期不再请求
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(vi.mocked(fetchGoalRepairAttempt)).toHaveBeenCalledTimes(1);
  });
});
