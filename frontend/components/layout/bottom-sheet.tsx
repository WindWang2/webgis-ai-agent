'use client'

/**
 * BottomSheet —— 移动档（≤768px）面板容器（ADR-0144 / P5）。
 *
 * 语义：拖拽把手 + 两档吸附（半屏 55% / 近全屏 92%）+ Escape/把手下拉关闭；
 * 焦点管理接入共享 useDialogFocus（与 settings/history 同一契约：初始聚焦、
 * document 级 Tab 围栏、Escape、关闭后焦点归还触发元素）。
 *
 * 与 drawer 的差别：sheet 从底部升起、占宽 100%、把手支持触摸拖拽换档；
 * 地图在 sheet 下方仍部分可见（半屏档），保证「地图全屏优先」不被完全遮死。
 */

import React, { useCallback, useRef, useState } from 'react'
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus'
import { useInertWhenClosed } from '@/lib/hooks/use-inert'
import { useT } from '@/lib/i18n/useT'

export type SheetSnap = 'half' | 'full'

export interface BottomSheetProps {
  open: boolean
  onClose: () => void
  /** sheet 的可访问名称（aria-labelledby 的目标 id）。 */
  titleId: string
  children: React.ReactNode
}

const SNAP_RATIO: Record<SheetSnap, string> = { half: '55%', full: '92%' }

/**
 * 指针 Y 坐标读取：jsdom 无 PointerEvent 时 React 合成事件的 clientY
 * 可能落到 nativeEvent expando 上 —— 双路兜底，真实浏览器两路同值。
 */
function readPointerY(e: React.PointerEvent<HTMLDivElement>): number {
  if (typeof e.clientY === 'number') return e.clientY
  const ny = (e.nativeEvent as unknown as { clientY?: number }).clientY
  return typeof ny === 'number' ? ny : 0
}

export function BottomSheet({ open, onClose, titleId, children }: BottomSheetProps) {
  const t = useT()
  const [snap, setSnap] = useState<SheetSnap>('half')
  const sheetRef = useRef<HTMLDivElement | null>(null)

  // 拖拽把手：pointer 跟随切换 snap 档（上拉超过 48px → full；下拉超过 48px → half；
  // 从任意档快速下拉超过 96px → 关闭）。轻量实现：不逐帧跟随位移，避免与
  // sheet 内容滚动的手势协商 —— 内容滚动走 touch 原生滚动。
  const dragStartY = useRef<number | null>(null)
  const pointerDownY = useRef<number | null>(null)

  const onHandleDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return
      const y = readPointerY(e)
      dragStartY.current = y
      pointerDownY.current = y
      // jsdom/老浏览器无 pointer capture —— 拖拽语义仍由本元素 move/up 承载
      try {
        e.currentTarget.setPointerCapture(e.pointerId)
      } catch {
        /* noop */
      }
    },
    []
  )

  const onHandleMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (dragStartY.current === null) return
      const dy = (dragStartY.current ?? 0) - readPointerY(e)
      if (dy > 48) setSnap('full')
      else if (dy < -48) setSnap('half')
    },
    []
  )

  const onHandleUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (pointerDownY.current !== null) {
        const totalDy = readPointerY(e) - pointerDownY.current
        if (totalDy > 96) {
          onClose()
        }
      }
      dragStartY.current = null
      pointerDownY.current = null
    },
    [onClose]
  )

  useInertWhenClosed(sheetRef, open)

  useDialogFocus({
    open,
    containerRef: sheetRef,
    onEscape: onClose,
  })

  if (!open) return null

  return (
    <>
      {/* scrim：点击关闭（与 drawers 同语义） */}
      <div
        className="fixed inset-0 z-[90] bg-surface-scrim"
        onClick={onClose}
        aria-hidden
      />
      <div
        ref={sheetRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="fixed inset-x-0 bottom-0 z-[91] flex flex-col rounded-t-lg border-t border-edge-subtle bg-surface-panel shadow-drawer"
        style={{
          height: SNAP_RATIO[snap],
          transition: dragStartY.current === null ? 'height 0.22s cubic-bezier(0.4, 0, 0.2, 1)' : 'none',
          paddingBottom: 'env(safe-area-inset-bottom, 0px)',
        }}
        data-snap={snap}
      >
        {/* 拖拽把手（含内边距 ≥44px 命中区目标） */}
        <div
          role="separator"
          aria-label={t('layout.sheet.handleAria')}
          aria-valuenow={snap === 'full' ? 2 : 1}
          aria-valuemin={1}
          aria-valuemax={2}
          data-testid="bottom-sheet-handle"
          onPointerDown={onHandleDown}
          onPointerMove={onHandleMove}
          onPointerUp={onHandleUp}
          onPointerCancel={onHandleUp}
          className="flex shrink-0 cursor-grab touch-none items-center justify-center"
          style={{ height: 44 }}
        >
          <span aria-hidden className="h-1 w-10 rounded-pill bg-edge-strong" />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">{children}</div>
      </div>
    </>
  )
}

export default BottomSheet
