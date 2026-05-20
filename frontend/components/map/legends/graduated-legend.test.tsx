import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { GraduatedLegend } from './graduated-legend';

const spec = {
  type: 'graduated' as const,
  field: 'pop',
  breaks: [0, 100, 500, 1000],
  palette: 'YlOrRd',
  palette_colors: ['#fff', '#aaa', '#000'],
};

describe('GraduatedLegend', () => {
  it('renders all class rows with break ranges', () => {
    render(<GraduatedLegend spec={spec} />);
    expect(screen.getByText(/pop/)).toBeInTheDocument();
    expect(screen.getByText(/0/)).toBeInTheDocument();
    // 3 classes => 3 rows
    expect(screen.getAllByRole('button').length).toBe(3);
  });

  it('clicking a row toggles visibility and fires onFilterChange', () => {
    const onFilterChange = vi.fn();
    render(<GraduatedLegend spec={spec} onFilterChange={onFilterChange} />);
    const rows = screen.getAllByRole('button');
    fireEvent.click(rows[0]);
    expect(onFilterChange).toHaveBeenCalled();
    // After toggling first class off, 2 ranges remain
    const ranges = onFilterChange.mock.calls.at(-1)?.[0];
    expect(ranges).toHaveLength(2);
  });
});
