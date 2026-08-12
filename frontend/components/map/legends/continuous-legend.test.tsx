import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ContinuousLegend } from './continuous-legend';

const spec = {
  type: 'continuous' as const,
  field: 'density',
  min: 0,
  max: 100,
  palette: 'Viridis',
  palette_colors: ['#440154', '#21908c', '#fde725'],
};

describe('ContinuousLegend', () => {
  // UI V4：数值格式统一到 formatLegendValue —— 之前 continuous 用 1 位小数
  // （0.0 / 100.0）而 graduated 用整数，同一份数据在两种图例里长得不一样。
  // 现在整数就是整数。
  it('renders min and max labels', () => {
    render(<ContinuousLegend spec={spec} />);
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
  });

  it('renders the field name', () => {
    render(<ContinuousLegend spec={spec} />);
    expect(screen.getByText(/density/)).toBeInTheDocument();
  });

  it('omits field row when no field given', () => {
    render(<ContinuousLegend spec={{ ...spec, field: undefined }} />);
    expect(screen.queryByText(/density/)).toBeNull();
  });

  // 默认脚注是「连续密度渲染」；divergent 复用本渲染器时必须能改写它，
  // 否则发散配色的图例会自称连续密度（V4 之前就是这个 bug）。
  it('renders the default renderer label, and honours an override', () => {
    const { unmount } = render(<ContinuousLegend spec={spec} />);
    expect(screen.getByText('连续密度渲染')).toBeInTheDocument();
    unmount();

    render(<ContinuousLegend spec={spec} kind="发散渐变渲染" />);
    expect(screen.getByText('发散渐变渲染')).toBeInTheDocument();
    expect(screen.queryByText('连续密度渲染')).toBeNull();
  });
});
