import { describe, expect, it, beforeEach, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RunTimeline } from './run-timeline';
import { RunEventsUnavailableError, type RunEventsPage } from '@/lib/api/geocompute';

vi.mock('@/lib/api/geocompute', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/geocompute')>('@/lib/api/geocompute');
  return {
    ...actual,
    getRunEvents: vi.fn(),
  };
});

const { getRunEvents } = await import('@/lib/api/geocompute');

function page(overrides: Partial<RunEventsPage> = {}): RunEventsPage {
  return {
    run_id: 'run-1',
    after_id: 2,
    count: 2,
    events: [
      { id: 1, run_id: 'run-1', event: 'run_started', created_at: '2026-09-11T02:00:00Z' },
      { id: 2, run_id: 'run-1', event: 'node_completed', node_id: 'clip', status: 'ok', rows: 12, created_at: '2026-09-11T02:00:05Z' },
    ],
    ...overrides,
  };
}

describe('RunTimeline（V7 Phase G）', () => {
  beforeEach(() => {
    cleanup();
    vi.mocked(getRunEvents).mockReset();
  });

  it('渲染事件时间线（时间/词表/节点/行数）', async () => {
    vi.mocked(getRunEvents).mockResolvedValue(page());
    render(<RunTimeline runId="run-1" />);
    await waitFor(() => expect(screen.getByTestId('run-timeline')).toBeTruthy());
    expect(screen.getByText('run_started')).toBeTruthy();
    expect(screen.getByText('node_completed')).toBeTruthy();
    expect(screen.getByText('clip')).toBeTruthy();
    expect(screen.getByText('12 行')).toBeTruthy();
  });

  it('404（trace 已清理）诚实披露而非静默空表', async () => {
    vi.mocked(getRunEvents).mockRejectedValue(new RunEventsUnavailableError('not_found'));
    render(<RunTimeline runId="run-1" />);
    await waitFor(() =>
      expect(screen.getByText(/执行事件不可用/)).toBeTruthy(),
    );
  });

  it('503（控制面不可用）显示可重试错误', async () => {
    vi.mocked(getRunEvents).mockRejectedValue(new RunEventsUnavailableError('unavailable'));
    render(<RunTimeline runId="run-1" />);
    await waitFor(() =>
      expect(screen.getByText(/执行控制面暂不可用/)).toBeTruthy(),
    );
  });

  it('有后续页时显示续读按钮并追加事件', async () => {
    // 首页取满 2 条且 after_id == 最后一条 id → hasMore
    vi.mocked(getRunEvents).mockResolvedValueOnce(page());
    vi.mocked(getRunEvents).mockResolvedValueOnce(page({
      after_id: 2,
      events: [
        { id: 3, run_id: 'run-1', event: 'run_completed', created_at: '2026-09-11T02:00:09Z' },
      ],
    }));
    render(<RunTimeline runId="run-1" />);
    await waitFor(() => expect(screen.getByRole('button', { name: '继续读取更早事件' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: '继续读取更早事件' }));
    await waitFor(() => expect(screen.getByText('run_completed')).toBeTruthy());
    expect(screen.getByText('run_started')).toBeTruthy();
  });
});
