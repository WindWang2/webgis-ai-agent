import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { H3LisaResultCard } from './h3-lisa-result-card';

describe('H3LisaResultCard', () => {
  it('renders null when result is empty', () => {
    const { container } = render(<H3LisaResultCard result={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders cluster counts and summary correctly', () => {
    const mockResult = {
      cluster_counts: { HH: 5, LL: 10, HL: 2, LH: 1, NS: 20 },
      value_field: 'income',
      summary: 'Found 5 High-High hotspots and 10 Low-Low coldspots.',
    };

    render(<H3LisaResultCard result={mockResult} layerId="layer-lisa-1" />);

    expect(screen.getByText('H3 LISA 空间聚类分析')).toBeInTheDocument();
    expect(screen.getByText('字段: income')).toBeInTheDocument();

    // Check cluster badges
    expect(screen.getByTestId('lisa-badge-HH')).toHaveTextContent('5');
    expect(screen.getByTestId('lisa-badge-LL')).toHaveTextContent('10');
    expect(screen.getByTestId('lisa-badge-HL')).toHaveTextContent('2');
    expect(screen.getByTestId('lisa-badge-LH')).toHaveTextContent('1');

    expect(screen.getByText(/Found 5 High-High hotspots/)).toBeInTheDocument();
    expect(screen.getByText(/累计显著聚类: 18 个网格/)).toBeInTheDocument();
  });

  it('fires onFocus when action button is clicked', () => {
    const onFocus = vi.fn();
    const mockResult = {
      cluster_counts: { HH: 3, LL: 0, HL: 0, LH: 0, NS: 5 },
      value_field: 'population',
    };

    render(<H3LisaResultCard result={mockResult} layerId="layer-lisa-42" onFocus={onFocus} />);

    const focusBtn = screen.getByRole('button', { name: /高亮图层/i });
    fireEvent.click(focusBtn);

    expect(onFocus).toHaveBeenCalledWith('layer-lisa-42');
  });
});
