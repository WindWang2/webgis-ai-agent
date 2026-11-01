/**
 * TemporalTimeController 组件测试（GOAL 03 M4）。
 *
 * 覆盖：未绑定不渲染（不伪造时间轴）/ 绑定后控件齐全 / displayed 门控
 * 标签 / seek 提交用户面 CAS / 缺期提示 / user_pinned 解除钮。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { commitMapSpecDocument } from '@/lib/mapspec/session-cursor';
import { commitUserTemporal } from '@/lib/temporal-map/mutation';
import { markLiveApplied, requestLiveFrame, resetTemporalLiveState } from '@/lib/temporal-map/live-frame';
import { TemporalTimeController } from './temporal-time-controller';

vi.mock('@/lib/temporal-map/mutation', () => ({
  commitUserTemporal: vi.fn(async () => ({ outcome: 'committed', revision: 2 })),
}));

vi.mock('@/lib/i18n/useT', () => ({
  useT: () => (key: string, opts?: { defaultValue?: string }) => opts?.defaultValue ?? key,
}));

vi.mock('@/lib/hooks/use-prefers-reduced-motion', () => ({
  usePrefersReducedMotion: () => false,
}));

const temporal = {
  schema_version: 'temporal_view.v1',
  enabled: true,
  layers: [{ layer_id: 'schools', source_ref: 'ref:schools' }],
  time_field: 'year',
  time_semantics: 'snapshot',
  time_representation: 'year_int',
  timezone: 'UTC',
  granularity: 'year',
  extent: ['2021-01-01T00:00:00Z', '2024-01-01T00:00:00Z'],
  frames: [
    { id: 'f-2021', label: '2021', start: '2021-01-01T00:00:00Z', end: '2022-01-01T00:00:00Z', feature_count: 2 },
    { id: 'f-2022', label: '2022', start: '2022-01-01T00:00:00Z', end: '2023-01-01T00:00:00Z', feature_count: 1 },
    { id: 'f-2023', label: '2023', start: '2023-01-01T00:00:00Z', end: '2024-01-01T00:00:00Z', feature_count: 3 },
  ],
  cursor_frame: 'f-2022',
  playback: { playing: false, rate: 1 },
  quality: ['missing_period_gap'],
  source_ref: 'ref:schools',
  content_revision: 7,
  user_pinned: false,
};

function commitSpec(temporalCfg: unknown): void {
  commitMapSpecDocument({ version: '1.8', layers: [], temporal: temporalCfg }, 1);
}

beforeEach(() => {
  resetTemporalLiveState();
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
  resetTemporalLiveState();
});

describe('TemporalTimeController', () => {
  it('未绑定 temporal → 不渲染（不伪造时间轴）', () => {
    commitMapSpecDocument({ version: '1.8', layers: [] }, 1);
    const { container } = render(<TemporalTimeController />);
    expect(container.firstChild).toBeNull();
  });

  it('绑定后渲染控件；displayed 门控前显示 cursor 帧', () => {
    commitSpec(temporal);
    render(<TemporalTimeController />);
    expect(screen.getByTestId('temporal-time-controller')).toBeTruthy();
    expect(screen.getByTestId('temporal-displayed-at').textContent).toBe('2022');
    expect(screen.getByTestId('temporal-seek')).toBeTruthy();
    expect(screen.getByTestId('temporal-rate')).toBeTruthy();
    expect(screen.getByTestId('temporal-missing-hint')).toBeTruthy();
  });

  it('displayed 标签只在应用回执后更新（门控）', async () => {
    commitSpec(temporal);
    render(<TemporalTimeController />);
    const frame23 = { id: 'f-2023', label: '2023', start: '2023-01-01T00:00:00Z', end: '2024-01-01T00:00:00Z' };
    requestLiveFrame(frame23 as never);
    // 已请求未回执：displayed 仍是 2022。
    expect(screen.getByTestId('temporal-displayed-at').textContent).toBe('2022');
    markLiveApplied(1, frame23 as never);
    await waitFor(() => {
      expect(screen.getByTestId('temporal-displayed-at').textContent).toBe('2023');
    });
  });

  it('seek → 用户面 CAS 提交（cursor + 停播）', async () => {
    commitSpec(temporal);
    render(<TemporalTimeController />);
    const slider = screen.getByTestId('temporal-seek') as HTMLInputElement;
    fireEvent.change(slider, { target: { value: '2' } });
    await waitFor(() => {
      expect(commitUserTemporal).toHaveBeenCalledWith(
        expect.objectContaining({ cursor_frame: 'f-2023', playback: { playing: false } }),
      );
    });
  });

  it('user_pinned → 显示解除钮；点击提交 unpin', async () => {
    commitSpec({ ...temporal, user_pinned: true });
    render(<TemporalTimeController />);
    const unpin = screen.getByTestId('temporal-unpin');
    fireEvent.click(unpin);
    await waitFor(() => {
      expect(commitUserTemporal).toHaveBeenCalledWith({ user_pinned: false });
    });
  });

  it('compare 启用 → 对照徽标显示 B 期', () => {
    commitSpec({ ...temporal, compare: { enabled: true, frame_id_b: 'f-2023' } });
    render(<TemporalTimeController />);
    expect(screen.getByTestId('temporal-compare-badge').textContent).toContain('2023');
  });
});
