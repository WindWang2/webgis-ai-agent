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
  it('renders min and max labels', () => {
    render(<ContinuousLegend spec={spec} />);
    expect(screen.getByText('0.0')).toBeInTheDocument();
    expect(screen.getByText('100.0')).toBeInTheDocument();
  });

  it('renders the field name', () => {
    render(<ContinuousLegend spec={spec} />);
    expect(screen.getByText(/density/)).toBeInTheDocument();
  });

  it('omits field row when no field given', () => {
    render(<ContinuousLegend spec={{ ...spec, field: undefined }} />);
    expect(screen.queryByText(/字段:/)).toBeNull();
  });
});
