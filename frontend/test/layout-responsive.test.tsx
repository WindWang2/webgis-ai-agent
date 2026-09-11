import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, act, createEvent } from '@testing-library/react'
import React from 'react'
import {
  useLayoutMode,
  currentLayoutMode,
  MOBILE_MAX_PX,
  THIN_MAX_PX,
} from '@/lib/hooks/use-layout-mode'
import { BottomSheet } from '@/components/layout/bottom-sheet'

type Listener = (e: { matches: boolean }) => void

class FakeMQL {
  private listeners = new Set<Listener>()
  constructor(public _query: string, public matches: boolean) {}
  addEventListener(_t: string, l: Listener) {
    this.listeners.add(l)
  }
  removeEventListener(_t: string, l: Listener) {
    this.listeners.delete(l)
  }
  addListener(l: Listener) {
    this.listeners.add(l)
  }
  removeListener(l: Listener) {
    this.listeners.delete(l)
  }
  set(matches: boolean) {
    this.matches = matches
    this.listeners.forEach((l) => l({ matches }))
  }
}

const queries = new Map<string, FakeMQL>()

function installMatchMedia(width: number) {
  const make = (query: string): FakeMQL => {
    // 同一 query 复用同一实例（跨用例持久，store 的监听器不丢）
    let mql = queries.get(query)
    if (!mql) {
      mql = new FakeMQL(query, false)
      queries.set(query, mql)
    }
    return mql
  }
  // 预建两档查询（store 订阅生命周期跨用例持久，实例不可中途更换）
  make(`(max-width: ${MOBILE_MAX_PX}px)`)
  make(`(min-width: ${MOBILE_MAX_PX + 1}px) and (max-width: ${THIN_MAX_PX}px)`)
  setWidth(width)

  // 组件读 window.matchMedia（jsdom 不内置）—— 直接装到 window
  ;(window as unknown as { matchMedia: unknown }).matchMedia = vi.fn(
    (query: string) => {
      const existing = queries.get(query)
      if (existing) return existing
      return make(query)
    }
  )
}

function setWidth(width: number) {
  for (const [query, mql] of queries) {
    const matches =
      query === `(max-width: ${MOBILE_MAX_PX}px)`
        ? width <= MOBILE_MAX_PX
        : width > MOBILE_MAX_PX && width <= THIN_MAX_PX
    if (mql.matches !== matches) act(() => mql.set(matches))
  }
}

describe('use-layout-mode 三档', () => {
  beforeEach(() => {
    installMatchMedia(1440)
  })
  afterEach(() => {
    delete (window as unknown as { matchMedia?: unknown }).matchMedia
  })

  function Probe() {
    const mode = useLayoutMode()
    return <span data-testid="mode">{mode}</span>
  }

  it('desktop（>1180）', () => {
    setWidth(1440)
    render(<Probe />)
    expect(screen.getByTestId('mode').textContent).toBe('desktop')
  })

  it('thin（769–1180）', () => {
    setWidth(1000)
    render(<Probe />)
    expect(screen.getByTestId('mode').textContent).toBe('thin')
  })

  it('mobile（≤768）', () => {
    setWidth(390)
    render(<Probe />)
    expect(screen.getByTestId('mode').textContent).toBe('mobile')
  })

  it('断点跨越即时切换', () => {
    setWidth(1440)
    render(<Probe />)
    expect(screen.getByTestId('mode').textContent).toBe('desktop')
    act(() => setWidth(390))
    expect(screen.getByTestId('mode').textContent).toBe('mobile')
    act(() => setWidth(1000))
    expect(screen.getByTestId('mode').textContent).toBe('thin')
    expect(currentLayoutMode()).toBe('thin')
  })
})

describe('BottomSheet', () => {
  beforeEach(() => {
    installMatchMedia(390)
  })
  afterEach(() => {
    delete (window as unknown as { matchMedia?: unknown }).matchMedia
  })

  it('关闭时渲染 null；打开时渲染 dialog + 把手', () => {
    const { rerender, container } = render(
      <BottomSheet open={false} onClose={() => {}} titleId="sheet-x">
        <div>content</div>
      </BottomSheet>
    )
    expect(container.querySelector('[role="dialog"]')).toBeNull()
    rerender(
      <BottomSheet open onClose={() => {}} titleId="sheet-x">
        <div>content</div>
      </BottomSheet>
    )
    expect(container.querySelector('[role="dialog"]')).toBeTruthy()
    expect(screen.getByTestId('bottom-sheet-handle')).toBeTruthy()
  })

  it('Escape 关闭（useDialogFocus 契约）', () => {
    const onClose = vi.fn()
    render(
      <BottomSheet open onClose={onClose} titleId="sheet-x">
        <button>inner</button>
      </BottomSheet>
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('scrim 点击关闭', () => {
    const onClose = vi.fn()
    const { container } = render(
      <BottomSheet open onClose={onClose} titleId="sheet-x">
        <div>content</div>
      </BottomSheet>
    )
    const scrim = container.querySelector('.bg-surface-scrim') as HTMLElement
    fireEvent.click(scrim)
    expect(onClose).toHaveBeenCalled()
  })

  it('把手下拉超过 96px 触发关闭（pointer 手势）', () => {
    const onClose = vi.fn()
    render(
      <BottomSheet open onClose={onClose} titleId="sheet-x">
        <div>content</div>
      </BottomSheet>
    )
    const handle = screen.getByTestId('bottom-sheet-handle')
    // 本环境的 fireEvent 会丢弃未识别 init 键（jsdom 无 PointerEvent）——
    // 用 createEvent + defineProperty 显式注入 clientY（组件双路读取兜底）。
    const down = createEvent.pointerDown(handle, { pointerId: 1, pointerType: 'touch', button: 0 })
    Object.defineProperty(down, 'clientY', { value: 400 })
    fireEvent(handle, down)
    const up = createEvent.pointerUp(handle, { pointerId: 1 })
    Object.defineProperty(up, 'clientY', { value: 520 })
    fireEvent(handle, up)
    expect(onClose).toHaveBeenCalled()
  })

  it('把手上拉切 full 档（data-snap）', () => {
    render(
      <BottomSheet open onClose={() => {}} titleId="sheet-x">
        <div>content</div>
      </BottomSheet>
    )
    const handle = screen.getByTestId('bottom-sheet-handle')
    const sheet = handle.closest('[role="dialog"]') as HTMLElement
    expect(sheet.getAttribute('data-snap')).toBe('half')
    const down = createEvent.pointerDown(handle, { pointerId: 1, pointerType: 'touch', button: 0 })
    Object.defineProperty(down, 'clientY', { value: 400 })
    fireEvent(handle, down)
    const move = createEvent.pointerMove(handle, { pointerId: 1 })
    Object.defineProperty(move, 'clientY', { value: 300 })
    fireEvent(handle, move)
    fireEvent.pointerUp(handle, { pointerId: 1 })
    expect(sheet.getAttribute('data-snap')).toBe('full')
  })
})
