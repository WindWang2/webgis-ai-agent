import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ThematicLegend } from './thematic-legend';

describe('ThematicLegend (router)', () => {
  it('routes graduated spec to GraduatedLegend', () => {
    render(<ThematicLegend spec={{
      type: 'graduated', field: 'pop',
      breaks: [0, 50, 100],
      palette: 'YlOrRd',
      palette_colors: ['#ff0', '#f00'],
    }} />);
    expect(screen.getByText(/pop/)).toBeInTheDocument();
  });

  it('routes continuous spec to ContinuousLegend', () => {
    render(<ThematicLegend spec={{
      type: 'continuous', field: 'd',
      min: 0, max: 1, palette: 'Viridis',
      palette_colors: ['#440154', '#fde725'],
    }} />);
    // UI V4：图例数值统一走 formatLegendValue（整数不再补 .0）。
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
  });

  it('routes categorical spec to CategoricalLegend', () => {
    render(<ThematicLegend spec={{
      type: 'categorical', field: 'pop',
      categories: [{ key: 'HH', color: '#f00', label: 'High-High' }],
    }} />);
    expect(screen.getByText('High-High')).toBeInTheDocument();
  });

  it('returns null for null legend_spec', () => {
    const { container } = render(<ThematicLegend spec={null as any} />);
    expect(container.firstChild).toBeNull();
  });
});
