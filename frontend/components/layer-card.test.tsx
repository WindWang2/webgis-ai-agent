import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LayerCard } from './layer-card';
import type { Layer } from '@/lib/types/layer';

const setEditingLayerId = vi.fn();

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector({
    setEditingLayerId,
  }),
}));

const mockLayer: Layer = {
  id: 'test-layer-1',
  name: 'Test Layer',
  type: 'vector',
  visible: true,
  opacity: 0.8,
};

describe('LayerCard', () => {
  const mockOnToggle = vi.fn();
  const mockOnDelete = vi.fn();
  const mockOnUpdate = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders layer name', () => {
    render(<LayerCard layer={mockLayer} onToggle={mockOnToggle} onDelete={mockOnDelete} />);
    expect(screen.getByText('Test Layer')).toBeInTheDocument();
  });

  it('renders type badge with Chinese label', () => {
    render(<LayerCard layer={mockLayer} onToggle={mockOnToggle} onDelete={mockOnDelete} />);
    expect(screen.getByText('矢量')).toBeInTheDocument();
  });

  it('renders opacity percentage', () => {
    render(<LayerCard layer={mockLayer} onToggle={mockOnToggle} onDelete={mockOnDelete} />);
    expect(screen.getByText('80%')).toBeInTheDocument();
  });

  it('calls onToggle when visibility button clicked', () => {
    render(<LayerCard layer={mockLayer} onToggle={mockOnToggle} onDelete={mockOnDelete} />);
    const visBtn = screen.getByTitle('隐藏');
    fireEvent.click(visBtn);
    expect(mockOnToggle).toHaveBeenCalledWith('test-layer-1', expect.anything());
  });

  it('calls onDelete when delete button clicked', () => {
    render(<LayerCard layer={mockLayer} onToggle={mockOnToggle} onDelete={mockOnDelete} />);
    const delBtn = screen.getByTitle('删除');
    fireEvent.click(delBtn);
    expect(mockOnDelete).toHaveBeenCalledWith('test-layer-1', expect.anything());
  });

  it('enters rename mode on double-click of name', () => {
    render(<LayerCard layer={mockLayer} onToggle={mockOnToggle} onDelete={mockOnDelete} onUpdate={mockOnUpdate} />);
    const nameSpan = screen.getByText('Test Layer');
    fireEvent.doubleClick(nameSpan);
    expect(screen.getByDisplayValue('Test Layer')).toBeInTheDocument();
  });
});
