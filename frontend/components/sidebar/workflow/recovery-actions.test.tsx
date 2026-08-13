import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { RecoveryActions } from './recovery-actions';
import type { WorkflowRunDetail } from '@/lib/api/project';

function run(overrides: Partial<WorkflowRunDetail> = {}): WorkflowRunDetail {
  return {
    id: 'r1',
    workflow_id: 'wf',
    workflow_version: 1,
    input_bindings: {},
    input_dataset_fingerprints: {},
    status: 'failed',
    execution_trace: [],
    outputs: {},
    cost_perf_summary: {},
    completed_steps: ['s1'],
    created_at: '',
    ...overrides,
  };
}

describe('RecoveryActions', () => {
  it('does not offer resume for a completed run', () => {
    render(
      <RecoveryActions
        run={run({ status: 'completed' })}
        busy={false}
        error={null}
        onReplay={vi.fn()}
        onResume={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: '尝试续跑' })).not.toBeInTheDocument();
  });

  it('disables actions while waiting for the backend', () => {
    render(
      <RecoveryActions run={run()} busy error={null} onReplay={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: '精确回放' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '尝试续跑' })).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('正在等待后端确认');
  });

  it('keeps exact vs latest distinct before confirm', async () => {
    const onReplay = vi.fn();
    render(
      <RecoveryActions run={run()} busy={false} error={null} onReplay={onReplay} onResume={vi.fn()} />,
    );
    fireEvent.click(screen.getByLabelText(/最新修订回放/));
    fireEvent.click(screen.getByRole('button', { name: '最新修订回放' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    fireEvent.click(screen.getByRole('button', { name: '确认最新修订回放？' }));
    expect(onReplay).toHaveBeenCalledWith('latest');
  });
});
