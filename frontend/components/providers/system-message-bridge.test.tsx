import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/components/ui/toast', () => {
  const store = { toasts: [] as unknown[], addToast: vi.fn(), removeToast: vi.fn() }
  return {
    useToastStore: Object.assign(
      (selector: (s: typeof store) => unknown) => selector(store),
      { getState: () => store },
    ),
  }
})

import { SystemMessageBridge } from './system-message-bridge'
import { useHudStore } from '@/lib/store/useHudStore'
import { useToastStore } from '@/components/ui/toast'

const DUP = '渲染器占用中，请稍候'

function addToastCalls(): unknown[][] {
  return vi.mocked(useToastStore.getState().addToast).mock.calls
}

function toastTexts(): unknown[] {
  return addToastCalls().map((c) => c[0])
}

describe('SystemMessageBridge', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useHudStore.setState({ pendingSystemMessage: null, _systemMessageQueue: [] })
    vi.mocked(useToastStore.getState().addToast).mockClear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('toasts every message when two identical strings are pushed back-to-back', () => {
    render(<SystemMessageBridge />)

    act(() => {
      useHudStore.getState().setPendingSystemMessage(DUP)
      useHudStore.getState().setPendingSystemMessage(DUP)
    })
    act(() => {
      vi.advanceTimersByTime(500)
    })
    act(() => {
      vi.advanceTimersByTime(500)
    })

    expect(toastTexts()).toEqual([DUP, DUP])
  })

  it('keeps the system-message channel alive after a duplicate re-activation', () => {
    render(<SystemMessageBridge />)

    act(() => {
      useHudStore.getState().setPendingSystemMessage(DUP)
      useHudStore.getState().setPendingSystemMessage(DUP)
    })
    act(() => {
      vi.advanceTimersByTime(500)
    })
    act(() => {
      vi.advanceTimersByTime(500)
    })

    act(() => {
      useHudStore.getState().setPendingSystemMessage('导出完成')
    })
    act(() => {
      vi.advanceTimersByTime(500)
    })

    expect(toastTexts()).toContain('导出完成')
  })
})
