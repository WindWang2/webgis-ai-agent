import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { Layer } from '@/lib/types/layer';

/**
 * FE-03: the opacity `<input type="range">` used to call `updateLayer` on every
 * onChange tick. Each tick rebuilt the whole layers array → the map-panel
 * reconcile effect fired on every drag step → worker spin-up + clone + layer
 * re-add. The fix debounces the store write: onChange updates only local slider
 * state; the store is updated once on commit (onPointerUp / onBlur).
 *
 * These tests pin that contract: dragging the slider must NOT call updateLayer
 * per tick; committing (pointer up / blur) calls it exactly once with the final
 * value.
 */

const updateLayer = vi.fn();
const toggleLayer = vi.fn();
const removeLayer = vi.fn();
const reorderLayers = vi.fn();

// A mutable store the component reads from. Tests swap `layers` per case via
// setStoreLayers(); the selector returns whatever the current store holds.
const store: Record<string, unknown> = {
  layers: [] as Layer[],
  toggleLayer,
  removeLayer,
  updateLayer,
  reorderLayers,
  theme: 'dark',
};

function setStoreLayers(layers: Layer[]) {
  store.layers = layers;
}

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector(store),
}));

// Import AFTER the mock is registered so the component picks up the mock.
import { LayersTab } from './layers-tab';

function makeLayer(overrides: Partial<Layer> = {}): Layer {
  return {
    id: 'L1',
    name: 'Layer One',
    type: 'vector',
    visible: true,
    opacity: 1,
    ...overrides,
  };
}

describe('LayersTab — opacity slider debounce (FE-03)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setStoreLayers([]);
  });

  it('does NOT call updateLayer on every onChange drag tick', () => {
    setStoreLayers([makeLayer({ opacity: 1 })]);
    render(<LayersTab />);

    const slider = screen.getByRole('slider') as HTMLInputElement;
    expect(slider).toBeInTheDocument();

    // Simulate a drag: several onChange ticks (100 → 80 → 60 → 40).
    fireEvent.change(slider, { target: { value: '100' } });
    fireEvent.change(slider, { target: { value: '80' } });
    fireEvent.change(slider, { target: { value: '60' } });
    fireEvent.change(slider, { target: { value: '40' } });

    // The store write must NOT happen per tick.
    expect(updateLayer).not.toHaveBeenCalled();
  });

  it('commits the final opacity to the store once on pointer up', () => {
    setStoreLayers([makeLayer({ opacity: 1 })]);
    render(<LayersTab />);

    const slider = screen.getByRole('slider') as HTMLInputElement;

    // Drag through several ticks...
    fireEvent.change(slider, { target: { value: '90' } });
    fireEvent.change(slider, { target: { value: '70' } });
    fireEvent.change(slider, { target: { value: '50' } });
    // ...then release the pointer (commit).
    fireEvent.pointerUp(slider);

    // Exactly one store write, carrying the final value (50% = 0.5).
    expect(updateLayer).toHaveBeenCalledTimes(1);
    expect(updateLayer).toHaveBeenCalledWith('L1', { opacity: 0.5 });
  });

  it('commits on blur as well (keyboard / focus-loss commit path)', () => {
    setStoreLayers([makeLayer({ opacity: 1 })]);
    render(<LayersTab />);

    const slider = screen.getByRole('slider') as HTMLInputElement;
    fireEvent.change(slider, { target: { value: '30' } });
    fireEvent.blur(slider);

    expect(updateLayer).toHaveBeenCalledTimes(1);
    expect(updateLayer).toHaveBeenCalledWith('L1', { opacity: 0.3 });
  });

  it('does not commit when the value did not change during a drag', () => {
    setStoreLayers([makeLayer({ opacity: 0.5 })]);
    render(<LayersTab />);

    const slider = screen.getByRole('slider') as HTMLInputElement;
    // Slider starts at 50%; user grabs + releases without moving.
    fireEvent.pointerUp(slider);

    expect(updateLayer).not.toHaveBeenCalled();
  });
});
