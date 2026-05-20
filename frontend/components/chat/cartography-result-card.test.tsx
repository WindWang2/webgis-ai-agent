import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { CartographyResultCard } from './cartography-result-card';

const result = {
  legend_spec: {
    type: 'graduated' as const,
    field: 'pop',
    breaks: [0, 100, 500, 1000],
    palette: 'YlOrRd',
    palette_colors: ['#fff', '#aaa', '#000'],
  },
  layer_meta: { title: '成都人口分布' },
};

describe('CartographyResultCard', () => {
  it('renders title and field info', () => {
    render(<CartographyResultCard result={result} layerId="layer-1" />);
    expect(screen.getByText('成都人口分布')).toBeInTheDocument();
    expect(screen.getByText(/pop/)).toBeInTheDocument();
  });

  it('renders palette swatches', () => {
    const { container } = render(<CartographyResultCard result={result} layerId="layer-1" />);
    const swatches = container.querySelectorAll('[data-testid="card-swatch"]');
    expect(swatches.length).toBe(3);
  });

  it('clicking 高亮 button calls onFocus with layerId', () => {
    const onFocus = vi.fn();
    render(<CartographyResultCard result={result} layerId="layer-1" onFocus={onFocus} />);
    fireEvent.click(screen.getByText(/高亮此图层/));
    expect(onFocus).toHaveBeenCalledWith('layer-1');
  });

  it('returns null when no legend_spec', () => {
    const { container } = render(<CartographyResultCard result={{}} layerId="x" />);
    expect(container.firstChild).toBeNull();
  });
});
