import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LayerStylePanel } from './layer-style-panel';
import type { Layer } from '@/lib/types/layer';

const updateLayer = vi.fn();
const setEditingLayerId = vi.fn();
let mockEditingLayerId: string | null = null;
let mockLayers: Layer[] = [];

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector({
    editingLayerId: mockEditingLayerId,
    layers: mockLayers,
    updateLayer,
    setEditingLayerId,
  }),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
vi.mock('framer-motion', () => {
  const fm = require('../../test/__mocks__/framer-motion');
  return { motion: fm.motion, AnimatePresence: fm.AnimatePresence };
});

describe('LayerStylePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockEditingLayerId = null;
    mockLayers = [];
  });

  it('renders nothing when no layer is being edited', () => {
    const { container } = render(<LayerStylePanel />);
    expect(container.innerHTML).toBe('');
  });

  it('renders layer name when editing', () => {
    const layer: Layer = { id: 'l1', name: 'Test Layer', type: 'vector', visible: true, opacity: 0.8 };
    mockEditingLayerId = 'l1';
    mockLayers = [layer];
    render(<LayerStylePanel />);
    expect(screen.getByText('Test Layer')).toBeInTheDocument();
  });

  it('shows vector controls for vector layer', () => {
    const layer: Layer = { id: 'l1', name: 'L', type: 'vector', visible: true, opacity: 0.8 };
    mockEditingLayerId = 'l1';
    mockLayers = [layer];
    render(<LayerStylePanel />);
    expect(screen.getByText('填充颜色')).toBeInTheDocument();
    expect(screen.getByText('描边颜色')).toBeInTheDocument();
    expect(screen.getByText(/描边宽度/)).toBeInTheDocument();
  });

  it('shows opacity control for all layer types', () => {
    const layer: Layer = { id: 'l1', name: 'L', type: 'raster', visible: true, opacity: 0.5 };
    mockEditingLayerId = 'l1';
    mockLayers = [layer];
    render(<LayerStylePanel />);
    expect(screen.getByText(/透明度/)).toBeInTheDocument();
  });

  it('calls setEditingLayerId(null) when close button clicked', () => {
    const layer: Layer = { id: 'l1', name: 'L', type: 'vector', visible: true, opacity: 0.8 };
    mockEditingLayerId = 'l1';
    mockLayers = [layer];
    render(<LayerStylePanel />);
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[0]);
    expect(setEditingLayerId).toHaveBeenCalledWith(null);
  });
});
