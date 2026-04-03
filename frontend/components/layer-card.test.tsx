import { render, screen, fireEvent } from '@testing-library/react'
import { LayerCard } from './layer-card'
import type { Layer } from '@/lib/types/layer'

const mockLayer: Layer = {
  id: 'test-layer-1',
  name: 'Test Layer',
  type: 'vector',
  visible: true,
  opacity: 0.8,
  source: 'test-source'
}

describe('LayerCard', () => {
  const mockOnToggle = vi.fn()
  const mockOnDelete = vi.fn()
  const mockOnEdit = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders layer information correctly', () => {
    render(
      <LayerCard
        layer={mockLayer}
        onToggle={mockOnToggle}
        onDelete={mockOnDelete}
        onEdit={mockOnEdit}
      />
    )

    expect(screen.getByText('Test Layer')).toBeInTheDocument()
    expect(screen.getByText('vector')).toBeInTheDocument()
    expect(screen.getByText('80%')).toBeInTheDocument()
    expect(screen.getByText('Source: test-source')).toBeInTheDocument()
    expect(screen.getByLabelText('Hide layer')).toBeInTheDocument()
  })

  it('shows EyeOff icon when layer is not visible', () => {
    const hiddenLayer = { ...mockLayer, visible: false }
    
    render(
      <LayerCard
        layer={hiddenLayer}
        onToggle={mockOnToggle}
        onDelete={mockOnDelete}
        onEdit={mockOnEdit}
      />
    )

    expect(screen.getByLabelText('Show layer')).toBeInTheDocument()
  })

  it('calls onToggle when visibility button is clicked', () => {
    render(
      <LayerCard
        layer={mockLayer}
        onToggle={mockOnToggle}
        onDelete={mockOnDelete}
        onEdit={mockOnEdit}
      />
    )

    fireEvent.click(screen.getByLabelText('Hide layer'))
    expect(mockOnToggle).toHaveBeenCalledWith('test-layer-1')
  })

  it('calls onEdit when edit button is clicked', () => {
    render(
      <LayerCard
        layer={mockLayer}
        onToggle={mockOnToggle}
        onDelete={mockOnDelete}
        onEdit={mockOnEdit}
      />
    )

    fireEvent.click(screen.getByText('Edit'))
    expect(mockOnEdit).toHaveBeenCalledWith(mockLayer)
  })

  it('calls onDelete when delete button is clicked', () => {
    render(
      <LayerCard
        layer={mockLayer}
        onToggle={mockOnToggle}
        onDelete={mockOnDelete}
        onEdit={mockOnEdit}
      />
    )

    fireEvent.click(screen.getByText('Delete'))
    expect(mockOnDelete).toHaveBeenCalledWith('test-layer-1')
  })
})
