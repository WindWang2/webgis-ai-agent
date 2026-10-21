import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { HistogramSliderWidget } from './histogram-slider';
import type { WidgetSpec } from '@/lib/copilot/affordance';

/**
 * #1746-7：断点竖线的 React key 不得只用 break 数值。
 *
 * 两个 range 滑块可以把断点拖成同值 → `key={brk}` 造出重复 key（React 警告 +
 * 可能的错位复用）。key 复合索引 `${brk}-${i}`。
 */

function spec(breaks: number[]): WidgetSpec {
  return {
    widget_id: 'w-hist-keys',
    kind: 'histogram_slider',
    title: 'NDVI 断点',
    payload: {
      field: 'ndvi',
      bins: [
        { lo: 0, hi: 10, count: 3 },
        { lo: 10, hi: 20, count: 7 },
        { lo: 20, hi: 30, count: 5 },
      ],
      breaks,
      min: 0,
      max: 30,
      layer_ref: 'ndvi_layer',
    },
  };
}

describe('HistogramSliderWidget 断点 key（#1746-7）', () => {
  const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

  afterEach(() => {
    errorSpy.mockClear();
  });

  it('同值断点不产生重复 key 警告，且两条竖线都在', () => {
    const { container } = render(<HistogramSliderWidget spec={spec([15, 15])} />);

    const lines = container.querySelectorAll('[data-testid="histogram-bars"] line');
    expect(lines).toHaveLength(2);
    expect(screen.getByTestId('break-slider-0')).toBeInTheDocument();
    expect(screen.getByTestId('break-slider-1')).toBeInTheDocument();

    const keyWarnings = errorSpy.mock.calls.filter((args) =>
      args.some((a) => typeof a === 'string' && a.includes('same key')),
    );
    expect(keyWarnings).toHaveLength(0);
  });

  it('常规（互异）断点渲染不回归', () => {
    const { container } = render(<HistogramSliderWidget spec={spec([5, 15, 25])} />);
    expect(container.querySelectorAll('[data-testid="histogram-bars"] line')).toHaveLength(3);
    expect(
      errorSpy.mock.calls.filter((args) =>
        args.some((a) => typeof a === 'string' && a.includes('same key')),
      ),
    ).toHaveLength(0);
  });
});
