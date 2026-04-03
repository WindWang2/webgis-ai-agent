import { render, screen, fireEvent } from '@testing-library/react'
import { SortControls } from './sort-controls'
import type { SortOption } from '@/lib/types/layer'

describe('SortControls', () => {
  const mockOnSort = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders with default values correctly', () => {
    render(<SortControls onSort={mockOnSort} />)

    expect(screen.getByRole('combobox')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Name')).toBeInTheDocument()
    expect(screen.getByLabelText('Asc')).toBeChecked()
    expect(screen.getByLabelText('Desc')).not.toBeChecked()
  })

  it('uses provided default values correctly', () => {
    render(
      <SortControls
        onSort={mockOnSort}
        defaultField="created_at"
        defaultOrder="desc"
      />
    )

    expect(screen.getByDisplayValue('Created')).toBeInTheDocument()
    expect(screen.getByLabelText('Desc')).toBeChecked()
    expect(screen.getByLabelText('Asc')).not.toBeChecked()
  })

  it('calls onSort when sort field is changed', () => {
    render(<SortControls onSort={mockOnSort} />)

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'updated_at' } })
    
    expect(mockOnSort).toHaveBeenCalledWith({
      field: 'updated_at',
      order: 'asc'
    })
  })

  it('calls onSort when sort order is changed to desc', () => {
    render(<SortControls onSort={mockOnSort} />)

    fireEvent.click(screen.getByLabelText('Desc'))
    
    expect(mockOnSort).toHaveBeenCalledWith({
      field: 'name',
      order: 'desc'
    })
  })

  it('calls onSort with updated values when both field and order are changed', () => {
    render(<SortControls onSort={mockOnSort} />)

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'created_at' } })
    fireEvent.click(screen.getByLabelText('Desc'))
    
    expect(mockOnSort).toHaveBeenNthCalledWith(1, {
      field: 'created_at',
      order: 'asc'
    })
    expect(mockOnSort).toHaveBeenNthCalledWith(2, {
      field: 'created_at',
      order: 'desc'
    })
  })

  it('renders all sort field options correctly', () => {
    render(<SortControls onSort={mockOnSort} />)

    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(3)
    expect(options[0]).toHaveValue('name')
    expect(options[0]).toHaveTextContent('Name')
    expect(options[1]).toHaveValue('created_at')
    expect(options[1]).toHaveTextContent('Created')
    expect(options[2]).toHaveValue('updated_at')
    expect(options[2]).toHaveTextContent('Updated')
  })
})
