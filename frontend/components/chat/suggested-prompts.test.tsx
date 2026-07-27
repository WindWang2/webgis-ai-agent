import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SuggestedPrompts } from './suggested-prompts'

vi.mock('framer-motion', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
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

  // 审计 findings.md A11y：key 必须稳定且唯一。锁定不会回退到会碰撞的 key 方案
  // （如重复文本作为 key，或裸索引加前缀）。React 会在开发态对重复 key 发出警告。
  it('uses unique keys — renders without a duplicate-key React warning', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      render(<SuggestedPrompts onSend={vi.fn()} />)
      const dupKeyWarnings = spy.mock.calls.filter(
        ([msg]) => typeof msg === 'string' && msg.includes('Encountered two children with the same key')
      )
      expect(dupKeyWarnings).toHaveLength(0)
    } finally {
      spy.mockRestore()
    }
  })
})
