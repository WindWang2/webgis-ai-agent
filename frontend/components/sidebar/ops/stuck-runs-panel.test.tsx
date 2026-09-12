/**
 * Stuck runs 干预面板测试（P3）—— 列表 / 确认对话框 / 回执 live region /
 * 任务中心联动 / 409 并发回执。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { StuckRunsPanel } from './stuck-runs-panel';
import type { UseStuckRunsResult } from '@/lib/hooks/use-cluster-stuck-runs';
import { stuckRunsFixture } from '@/test/fixtures/geocompute-fixtures';

function makeStuck(overrides: Partial<UseStuckRunsResult> = {}): UseStuckRunsResult {
  return {
    data: { runs: stuckRunsFixture.runs, count: stuckRunsFixture.count },
    loading: false,
    error: null,
    lastError: null,
    status: { lastFetchedAt: new Date().toISOString(), consecutiveErrors: 0, paused: false, pauseReason: null },
    channel: 'live',
    resetting: new Set(),
    receipts: [],
    resetRun: vi.fn().mockResolvedValue(undefined),
    refresh: vi.fn(),
    ...overrides,
  } as UseStuckRunsResult;
}

describe('StuckRunsPanel（P3 干预）', () => {
  it('渲染卡住时长 / 心跳 / 尝试次数', () => {
    render(<StuckRunsPanel stuck={makeStuck()} />);
    expect(screen.getByTestId('stuck-row-run-stuck-001')).toBeInTheDocument();
    expect(screen.getAllByText(/卡住/).length).toBeGreaterThan(0);
    expect(screen.getByText(/尝试 3 次 \/ epoch 4/)).toBeInTheDocument();
    expect(screen.getByTestId('requeue-run-stuck-001')).toBeInTheDocument();
    expect(screen.getByTestId('evict-run-stuck-002')).toBeInTheDocument();
  });

  it('空态：当前无卡住 run', () => {
    render(<StuckRunsPanel stuck={makeStuck({ data: { runs: [], count: 0 } })} />);
    expect(screen.getByText('当前无卡住 run')).toBeInTheDocument();
  });

  it('重派走确认对话框 → 确认后调用 resetRun(requeued)', async () => {
    const resetRun = vi.fn().mockResolvedValue(undefined);
    render(<StuckRunsPanel stuck={makeStuck({ resetRun })} />);
    fireEvent.click(screen.getByTestId('requeue-run-stuck-001'));
    // 确认对话框出现（不可逆干预必须二次确认）；确认按钮与行内按钮同名 → 限定 dialog 范围
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('确认重派该 run？');
    fireEvent.click(within(dialog).getByRole('button', { name: '重派' }));
    await waitFor(() => expect(resetRun).toHaveBeenCalledWith('run-stuck-001', 'requeued'));
  });

  it('驱逐走确认对话框 → resetRun(failed)', async () => {
    const resetRun = vi.fn().mockResolvedValue(undefined);
    render(<StuckRunsPanel stuck={makeStuck({ resetRun })} />);
    fireEvent.click(screen.getByTestId('evict-run-stuck-001'));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('确认隔离驱逐该 run？');
    fireEvent.click(within(dialog).getByRole('button', { name: '驱逐' }));
    await waitFor(() => expect(resetRun).toHaveBeenCalledWith('run-stuck-001', 'failed'));
  });

  it('取消对话框不触发动作', () => {
    const resetRun = vi.fn();
    render(<StuckRunsPanel stuck={makeStuck({ resetRun })} />);
    fireEvent.click(screen.getByTestId('requeue-run-stuck-001'));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(resetRun).not.toHaveBeenCalled();
  });

  it('回执 live region 播报结果 + 任务中心联动', async () => {
    const { rerender } = render(<StuckRunsPanel stuck={makeStuck()} />);
    // 模拟 hook 内部 reset 后 receipts 更新
    rerender(
      <StuckRunsPanel
        stuck={makeStuck({
          receipts: [{ runId: 'run-stuck-001', outcome: 'requeued', at: new Date().toISOString() }],
        })}
      />,
    );
    const region = screen.getByTestId('stuck-receipts');
    expect(region).toHaveAttribute('aria-live', 'polite');
    expect(region).toHaveTextContent(/已重新入队：run-stuck-001/);
    fireEvent.click(screen.getByRole('button', { name: /任务中心/ }));
  });

  it('409 并发回执显示警告而非错误', () => {
    render(
      <StuckRunsPanel
        stuck={makeStuck({
          receipts: [
            { runId: 'run-stuck-002', outcome: 'requeued', at: new Date().toISOString(), warning: '该 run 已离开 stuck 状态（可能已被并发处理）' },
          ],
        })}
      />,
    );
    expect(screen.getByText(/已被并发处理/)).toBeInTheDocument();
  });
});
