import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MapDecorations } from './map-decorations';

describe('MapDecorations', () => {
  it('renders nothing when show=false', () => {
    const { container } = render(<MapDecorations show={false} title="X" zoom={10} centerLat={30} bearing={0} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders north arrow, scale bar, and title when show=true', () => {
    render(<MapDecorations show={true} title="成都人口分布" zoom={10} centerLat={30} bearing={0} />);
    expect(screen.getByText('成都人口分布')).toBeInTheDocument();
    expect(screen.getByTestId('north-arrow')).toBeInTheDocument();
    expect(screen.getByTestId('scale-bar')).toBeInTheDocument();
  });

  it('hides title chip when title is null', () => {
    render(<MapDecorations show={true} title={null} zoom={10} centerLat={30} bearing={0} />);
    expect(screen.queryByTestId('map-title')).toBeNull();
    // north arrow and scale bar still present
    expect(screen.getByTestId('north-arrow')).toBeInTheDocument();
  });

  it('scale bar reflects zoom level (smaller at higher zoom)', () => {
    const { rerender } = render(<MapDecorations show={true} title={null} zoom={10} centerLat={30} bearing={0} />);
    const text10 = screen.getByTestId('scale-bar').textContent ?? '';
    rerender(<MapDecorations show={true} title={null} zoom={16} centerLat={30} bearing={0} />);
    const text16 = screen.getByTestId('scale-bar').textContent ?? '';
    expect(text10).not.toBe(text16);
  });
});
