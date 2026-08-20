import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { GraduatedLegend } from './graduated-legend';

function makeSpec(overrides?: Partial<import('@/lib/map-kit/types').GraduatedLegendSpec>): import('@/lib/map-kit/types').GraduatedLegendSpec {
  return {
    type: 'graduated',
    field: 'pop',
    breaks: [0, 10, 20, 30],
    palette: 'a',
    palette_colors: ['#fff', '#aaa', '#000'],
    ...overrides,
  };
}

describe('GraduatedLegend — filter sync (#689 fix 4)', () => {
  it('calls onFilterChange with full ranges when spec changes (classCount/specKey change)', () => {
    const onFilterChange = vi.fn();
    const spec1 = makeSpec({ breaks: [0, 10, 20, 30] });
    const { rerender } = render(<GraduatedLegend spec={spec1} onFilterChange={onFilterChange} />);
    onFilterChange.mockClear();
    // New spec with different breaks -> should notify to clear stale filter
    const spec2 = makeSpec({ breaks: [0, 5, 15, 25, 35] });
    rerender(<GraduatedLegend spec={spec2} onFilterChange={onFilterChange} />);
    expect(onFilterChange).toHaveBeenCalledTimes(1);
    // Full-visible ranges for spec2: [[0,5],[5,15],[15,25],[25,35]]
    expect(onFilterChange).toHaveBeenCalledWith([
      [0, 5],
      [5, 15],
      [15, 25],
      [25, 35],
    ]);
  });

  it('toggling then spec change still clears stale filter', () => {
    const onFilterChange = vi.fn();
    const spec = makeSpec({ breaks: [0, 10, 20] });
    const { rerender } = render(<GraduatedLegend spec={spec} onFilterChange={onFilterChange} />);
    // Hide one class
    const firstRow = screen.getAllByRole('button')[0];
    fireEvent.click(firstRow);
    expect(onFilterChange).toHaveBeenLastCalledWith([[10, 20]]);
    onFilterChange.mockClear();
    // Agent re-styles with new breaks
    const newSpec = makeSpec({ breaks: [0, 5, 15, 25] });
    rerender(<GraduatedLegend spec={newSpec} onFilterChange={onFilterChange} />);
    expect(onFilterChange).toHaveBeenCalledWith([
      [0, 5],
      [5, 15],
      [15, 25],
    ]);
  });
});

