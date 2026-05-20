import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { CategoricalLegend } from './categorical-legend';

const lisaSpec = {
  type: 'categorical' as const,
  field: 'pop',
  categories: [
    { key: 'HH', color: '#ff0000', label: 'High-High' },
    { key: 'LL', color: '#0000ff', label: 'Low-Low' },
    { key: 'HL', color: '#ffaaaa', label: 'High-Low' },
    { key: 'LH', color: '#aaaaff', label: 'Low-High' },
    { key: 'NS', color: '#cccccc', label: 'Not Significant' },
  ],
};

describe('CategoricalLegend', () => {
  it('renders all category labels', () => {
    render(<CategoricalLegend spec={lisaSpec} />);
    expect(screen.getByText('High-High')).toBeInTheDocument();
    expect(screen.getByText('Low-Low')).toBeInTheDocument();
    expect(screen.getByText('Not Significant')).toBeInTheDocument();
  });

  it('renders 5 color swatches', () => {
    const { container } = render(<CategoricalLegend spec={lisaSpec} />);
    const swatches = container.querySelectorAll('[data-testid="cat-swatch"]');
    expect(swatches.length).toBe(5);
  });
});
