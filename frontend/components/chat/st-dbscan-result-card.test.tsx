import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { StDbscanResultCard } from './st-dbscan-result-card';

describe('StDbscanResultCard', () => {
  const mockResult = {
    stats: {
      total_clusters: 3,
      clustered_points: 15,
      noise_points: 2,
      temporal_span_hours: 4.5,
      eps1_spatial_meters: 500,
      eps2_temporal_seconds: 1800,
    },
    summary: 'ST-DBSCAN identified 3 spatio-temporal cluster(s) and 2 noise point(s).',
  };

  it('renders nothing when result is null', () => {
    const { container } = render(<StDbscanResultCard result={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders stats badges correctly', () => {
    render(<StDbscanResultCard result={mockResult} />);
    expect(screen.getByText('ST-DBSCAN 时空聚类分析')).toBeInTheDocument();
    expect(screen.getByTestId('st-badge-clusters')).toHaveTextContent('3');
    expect(screen.getByTestId('st-badge-clustered')).toHaveTextContent('15');
    expect(screen.getByTestId('st-badge-noise')).toHaveTextContent('2');
    expect(screen.getByTestId('st-badge-span')).toHaveTextContent('4.5h');
  });

  it('handles play pause toggle and slider input', () => {
    const onFrameChange = vi.fn();
    render(<StDbscanResultCard result={mockResult} onFrameChange={onFrameChange} />);

    const playBtn = screen.getByTestId('play-pause-button');
    expect(playBtn).toHaveAttribute('aria-label', '播放演变动画');

    fireEvent.click(playBtn);
    expect(playBtn).toHaveAttribute('aria-label', '暂停演变动画');

    const slider = screen.getByRole('slider', { name: '时间轴滑块' });
    fireEvent.change(slider, { target: { value: '50' } });

    expect(onFrameChange).toHaveBeenCalledWith(50);
  });

  it('triggers onFocus when highlight layer is clicked', () => {
    const onFocus = vi.fn();
    render(<StDbscanResultCard result={mockResult} layerId="layer-123" onFocus={onFocus} />);

    const focusBtn = screen.getByRole('button', { name: /高亮图层/i });
    fireEvent.click(focusBtn);

    expect(onFocus).toHaveBeenCalledWith('layer-123');
  });
});
