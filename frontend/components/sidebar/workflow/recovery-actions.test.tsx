import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

// #528：回放/续跑走后端写路径（#501 要求认证）—— 默认按已登录渲染使既有
// 用例不变；匿名用例显式置空 mockUser。
let mockUser: { id: string; username: string } | null = { id: 'u1', username: 'ops' };
vi.mock('@/lib/auth/tokenStore', () => ({
  getAuthUser: () => mockUser,
  subscribeAuth: () => () => {},
}));

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

beforeEach(() => {
  mockUser = { id: 'u1', username: 'ops' };
});

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

  it('匿名时回放/续跑禁用且给出登录提示，点击不触发 onReplay/#528', async () => {
    mockUser = null;
    const onReplay = vi.fn();
    render(
      <RecoveryActions run={run()} busy={false} error={null} onReplay={onReplay} onResume={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: '精确回放' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '尝试续跑' })).toBeDisabled();
    expect(screen.getByText(/需要登录账号/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '精确回放' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 260));
    });
    expect(screen.queryByRole('button', { name: '确认精确回放？' })).not.toBeInTheDocument();
    expect(onReplay).not.toHaveBeenCalled();
  });

  it('登录后回放按钮恢复可用', () => {
    render(
      <RecoveryActions run={run()} busy={false} error={null} onReplay={vi.fn()} onResume={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: '精确回放' })).toBeEnabled();
  });
});
