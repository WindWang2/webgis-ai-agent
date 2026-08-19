import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { Layer } from '@/lib/types/layer';

const updateLayer = vi.fn();
const toggleLayer = vi.fn();

const store: Record<string, unknown> = {
  layers: [] as Layer[],
  toggleLayer,
  updateLayer,
};

const mutationMocks = vi.hoisted(() => ({
  setLayerOpacityAndCommit: vi.fn(),
  toggleLayerAndCommit: vi.fn(),
  removeLayerAndCommit: vi.fn(),
  reorderLayersAndCommit: vi.fn(),
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: Object.assign(
    (selector: (s: any) => any) => selector(store),
    { getState: () => store },
  ),
}));

vi.mock('@/lib/mapspec/user-mutation', () => mutationMocks);

import { LayerManagement } from './layer-management';

function makeLayer(overrides: Partial<Layer> = {}): Layer {
  return {
    id: 'L1',
    name: 'Schools',
    type: 'vector',
    visible: true,
    opacity: 1,
    ...overrides,
  };
}

describe('LayerManagement — MapSpec presentation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    store.layers = [];
  });

  it('commits opacity to MapSpec on pointer up, not HUD-only', () => {
    store.layers = [makeLayer({ opacity: 1 })];
    render(<LayerManagement />);

    const slider = screen.getByRole('slider') as HTMLInputElement;
    fireEvent.change(slider, { target: { value: '40' } });
    (store.layers as Layer[])[0] = makeLayer({ opacity: 0.4 });
    fireEvent.pointerUp(slider);

    expect(mutationMocks.setLayerOpacityAndCommit).toHaveBeenCalledWith('L1', 0.4);
  });

  it('toggles visibility through toggleLayerAndCommit so a 409 can roll HUD back', () => {
    store.layers = [makeLayer({ visible: true })];
    render(<LayerManagement />);

    fireEvent.click(screen.getByLabelText('显示图层：Schools'));

    expect(mutationMocks.toggleLayerAndCommit).toHaveBeenCalledWith('L1');
    expect(toggleLayer).not.toHaveBeenCalled();
  });
});
