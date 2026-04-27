import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SuggestedPrompts } from './suggested-prompts'

vi.mock('framer-motion', () => {
  const actual = require('../../test/__mocks__/framer-motion')
  return actual.default || actual
})

describe('SuggestedPrompts', () => {
  it('renders 4 suggestion buttons', () => {
    const onSend = vi.fn()
    render(<SuggestedPrompts onSend={onSend} />)
    expect(screen.getAllByRole('button')).toHaveLength(4)
  })

  it('calls onSend with correct text on click', () => {
    const onSend = vi.fn()
    render(<SuggestedPrompts onSend={onSend} />)
    fireEvent.click(screen.getByText('计算NDVI植被指数'))
    expect(onSend).toHaveBeenCalledWith('计算NDVI植被指数')
  })
})
