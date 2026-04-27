import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { TaskProgress } from './task-progress';
import type { TaskState } from '@/lib/store/useHudStore';
import { createMockTaskStep, createMockTaskState } from '@/test/test-utils';

vi.mock('@/lib/api/task', () => ({
  cancelTask: vi.fn().mockResolvedValue(undefined),
}));

import { cancelTask } from '@/lib/api/task';

describe('TaskProgress', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const runningTask: TaskState = createMockTaskState({
    id: 'task-abc123def456',
    status: 'running',
    stepCount: 4,
    steps: [
      createMockTaskStep({ id: 's1', tool: 'query_osm_poi', status: 'completed', stepIndex: 0 }),
      createMockTaskStep({ id: 's2', tool: 'buffer_analysis', status: 'completed', stepIndex: 1 }),
      createMockTaskStep({ id: 's3', tool: 'overlay_analysis', status: 'running', stepIndex: 2 }),
    ],
  });

  it('renders task ID prefix', () => {
    render(<TaskProgress task={runningTask} />);
    expect(screen.getByText(/任务 ID: task\.\.\./)).toBeInTheDocument();
  });

  it('shows running status label', () => {
    render(<TaskProgress task={runningTask} />);
    expect(screen.getByText('航道探索中')).toBeInTheDocument();
  });

  it('shows completed status label', () => {
    const task = createMockTaskState({ status: 'completed', summary: 'Done' });
    render(<TaskProgress task={task} />);
    expect(screen.getByText('探索达成')).toBeInTheDocument();
  });

  it('shows failed status label with error styling', () => {
    const task = createMockTaskState({
      status: 'failed',
      steps: [createMockTaskStep({ id: 's1', status: 'failed', error: 'timeout' })],
    });
    render(<TaskProgress task={task} />);
    expect(screen.getByText('遭遇暗礁')).toBeInTheDocument();
    expect(screen.getByText('timeout')).toBeInTheDocument();
  });

  it('displays progress percentage', () => {
    render(<TaskProgress task={runningTask} />);
    expect(screen.getByText('50%')).toBeInTheDocument();
  });

  it('renders step tool names', () => {
    render(<TaskProgress task={runningTask} />);
    expect(screen.getByText('[query_osm_poi]')).toBeInTheDocument();
    expect(screen.getByText('[buffer_analysis]')).toBeInTheDocument();
  });

  it('shows cancel button when running', () => {
    render(<TaskProgress task={runningTask} />);
    expect(screen.getByText('中止航程')).toBeInTheDocument();
  });

  it('hides cancel button when completed', () => {
    const task = createMockTaskState({ status: 'completed' });
    render(<TaskProgress task={task} />);
    expect(screen.queryByText('中止航程')).not.toBeInTheDocument();
  });

  it('calls cancelTask when cancel button clicked', () => {
    render(<TaskProgress task={runningTask} />);
    fireEvent.click(screen.getByText('中止航程'));
    expect(cancelTask).toHaveBeenCalledWith('task-abc123def456');
  });

  it('auto-collapses after 3 seconds when completed', async () => {
    vi.useRealTimers();
    const task = createMockTaskState({ status: 'completed' });
    const { container } = render(<TaskProgress task={task} />);
    // Initially expanded
    expect(container.querySelector('.max-h-96')).toBeTruthy();
    // Wait for the 3s timer to collapse
    await new Promise(r => setTimeout(r, 3100));
    expect(container.querySelector('.max-h-0')).toBeTruthy();
  });
});
