import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { makeCubeWindowResult } from '@/test/lakehouse/fixtures';

/**
 * 时序播放器组件契约（P7）：
 * - reduced-motion → 播放禁用（仅手动步进）；
 * - 键盘 ←/→ 步进、Space 播放暂停（非 reduced-motion）；
 * - colorbar 图例联动渲染；当前步标签跟随。
 */
const matchMediaState = { matches: false };
vi.stubGlobal(
  'matchMedia',
  vi.fn().mockImplementation((query: string) => ({
    matches: matchMediaState.matches,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
  })),
);

import { TimelinePlayer } from './timeline-player';

function makeResult(times: number) {
  const result = makeCubeWindowResult();
  result.bands = {
    band_0: Array.from({ length: times }, (_, t) =>
      Array.from({ length: 2 }, (_, y) => Array.from({ length: 2 }, (_, x) => t + x + y)),
    ),
  };
  result.times = Array.from(
    { length: times },
    (_, t) => `2026-01-${String(t + 1).padStart(2, '0')}T00:00:00`,
  );
  return result;
}

describe('TimelinePlayer', () => {
  it('渲染步数 / colorbar / 当前步标签', () => {
    render(<TimelinePlayer result={makeResult(6)} onClose={() => {}} />);
    expect(screen.getByText('6 步')).toBeInTheDocument();
    expect(screen.getByTestId('lakehouse-colorbar')).toBeInTheDocument();
    expect(screen.getByTestId('lakehouse-timeline-current')).toHaveTextContent('2026-01-01 00:00');
  });

  it('方向键步进（键盘可达）', () => {
    render(<TimelinePlayer result={makeResult(4)} onClose={() => {}} />);
    fireEvent.keyDown(screen.getByTestId('lakehouse-timeline-player'), { key: 'ArrowRight' });
    expect(screen.getByTestId('lakehouse-timeline-current')).toHaveTextContent('2026-01-02');
    fireEvent.keyDown(screen.getByTestId('lakehouse-timeline-player'), { key: 'ArrowLeft' });
    expect(screen.getByTestId('lakehouse-timeline-current')).toHaveTextContent('2026-01-01');
  });

  it('reduced-motion：播放禁用 + 提示；步进仍可用', () => {
    matchMediaState.matches = true;
    render(<TimelinePlayer result={makeResult(4)} onClose={() => {}} />);
    expect(screen.getByTestId('lakehouse-timeline-play')).toBeDisabled();
    expect(screen.getByTitle(/减弱动态效果/)).toBeInTheDocument();
    fireEvent.keyDown(screen.getByTestId('lakehouse-timeline-player'), { key: 'ArrowRight' });
    expect(screen.getByTestId('lakehouse-timeline-current')).toHaveTextContent('2026-01-02');
    matchMediaState.matches = false;
  });
});
