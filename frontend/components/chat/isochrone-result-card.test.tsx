import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { IsochroneResultCard } from './isochrone-result-card';

describe('IsochroneResultCard', () => {
  it('renders null when result is empty', () => {
    const { container } = render(<IsochroneResultCard result={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders travel time, mode, facility count, and area', () => {
    const mockResult = {
      travel_time_min: 15,
      mode: 'walking',
      facility_count: 3,
      area_km2: 12.45,
      summary: 'Generated 15-minute walking service area polygons.',
    };

    render(<IsochroneResultCard result={mockResult} layerId="layer-iso-1" />);

    expect(screen.getByText('等时圈网络分析 (15 分钟)')).toBeInTheDocument();
    expect(screen.getByText('步行模式')).toBeInTheDocument();
    expect(screen.getByText('3 个设施')).toBeInTheDocument();
    expect(screen.getByText('12.45 km²')).toBeInTheDocument();
    expect(screen.getByText('Generated 15-minute walking service area polygons.')).toBeInTheDocument();
  });

  it('handles driving travel mode correctly', () => {
    const mockResult = {
      travel_time_min: 30,
      mode: 'driving',
      facility_count: 1,
      area_km2: 45.8,
    };

    render(<IsochroneResultCard result={mockResult} layerId="layer-iso-driving" />);

    expect(screen.getByText('等时圈网络分析 (30 分钟)')).toBeInTheDocument();
    expect(screen.getByText('驾车模式')).toBeInTheDocument();
    expect(screen.getByText('速度基准: 400m/min')).toBeInTheDocument();
  });

  it('triggers onFocus callback when clicked', () => {
    const onFocus = vi.fn();
    const mockResult = {
      travel_time_min: 10,
      mode: 'walking',
      facility_count: 2,
    };

    render(<IsochroneResultCard result={mockResult} layerId="layer-iso-focus" onFocus={onFocus} />);

    const focusBtn = screen.getByRole('button', { name: /高亮图层/i });
    fireEvent.click(focusBtn);

    expect(onFocus).toHaveBeenCalledWith('layer-iso-focus');
  });
});
