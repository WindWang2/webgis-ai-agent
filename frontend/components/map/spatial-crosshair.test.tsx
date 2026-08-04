import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, act } from '@testing-library/react';
import { SpatialCrosshair } from './spatial-crosshair';
import { useHudStore } from '@/lib/store/useHudStore';

describe('SpatialCrosshair', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useHudStore.setState({
      viewport: { center: [116.4074, 39.9042], zoom: 10, bearing: 0, pitch: 0 },
      aiStatus: 'idle',
      accentColor: '#16a34a',
      is3D: false,
    });
    // Mock navigator.clipboard
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders correctly and copies coordinates on reticle click with timer cleanup', () => {
    const { container, unmount } = render(<SpatialCrosshair />);
    const svg = container.querySelector('svg');
    expect(svg).toBeDefined();

    if (svg) {
      act(() => {
        fireEvent.click(svg);
      });
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('116.407400, 39.904200');

      // Click again to test timer reset
      act(() => {
        fireEvent.click(svg);
      });

      // Fast-forward timers
      act(() => {
        vi.advanceTimersByTime(1500);
      });
    }

    // Unmount to verify no errors from dangling timers
    unmount();
  });
});
