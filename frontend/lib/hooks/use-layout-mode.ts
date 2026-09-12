'use client'

/**
 * 布局模式 store（ADR-0144 / P5）—— 三档真布局切换的唯一真相。
 *
 * - desktop（>1180px）：现状桌面壳（左栏 dock + 拖拽调宽）
 * - thin（769–1180px）：侧栏收窄为图标轨语义（面板覆盖模式），拖拽调宽禁用
 * - mobile（≤768px）：NavRail 折叠 + 面板 bottom-sheet 化 + 地图全屏优先
 *
 * 断点选择：768 沿用既有 LEFT_PANEL_MIN_VIEWPORT_PX 语义（#999/初始开合），
 * 避免同一像素常量出现第二含义；1180 是「面板(280) + 地图(约 700 可用)」
 * 之下开始挤压工作区的经验下限。
 *
 * 实现：模块级 matchMedia 双查询 + useSyncExternalStore，SSR 返回 desktop；
 * 断点跨越才触发重渲染（与 use-small-viewport 同款纪律）。
 */

import { useSyncExternalStore } from 'react'

export const LAYOUT_MODES = ['desktop', 'thin', 'mobile'] as const

export type LayoutMode = (typeof LAYOUT_MODES)[number]

export const MOBILE_MAX_PX = 768
export const THIN_MAX_PX = 1180

const MOBILE_QUERY = `(max-width: ${MOBILE_MAX_PX}px)`
const THIN_QUERY = `(min-width: ${MOBILE_MAX_PX + 1}px) and (max-width: ${THIN_MAX_PX}px)`

function resolveMode(): LayoutMode {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return 'desktop'
  }
  if (window.matchMedia(MOBILE_QUERY).matches) return 'mobile'
  if (window.matchMedia(THIN_QUERY).matches) return 'thin'
  return 'desktop'
}

let cachedMode: LayoutMode = 'desktop'
let initialized = false
const listeners = new Set<() => void>()

function mql(query: string): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null
  try {
    return window.matchMedia(query)
  } catch {
    return null
  }
}

function recompute(): void {
  const next = resolveMode()
  if (next !== cachedMode) {
    cachedMode = next
    listeners.forEach((l) => l())
  }
}

function ensureInit(): void {
  if (initialized || typeof window === 'undefined') return
  initialized = true
  cachedMode = resolveMode()
  for (const q of [MOBILE_QUERY, THIN_QUERY]) {
    const list = mql(q)
    if (list) {
      if (typeof list.addEventListener === 'function') {
        list.addEventListener('change', recompute)
      } else if (typeof list.addListener === 'function') {
        list.addListener(recompute) // 旧 Safari/jsdom 兜底
      }
    }
  }
}

function subscribe(listener: () => void): () => void {
  ensureInit()
  listeners.add(listener)
  // 首个订阅者可能在初始化前错过了模式解析 —— 订阅时强制对齐一次。
  recompute()
  return () => {
    listeners.delete(listener)
  }
}

function getSnapshot(): LayoutMode {
  ensureInit()
  return cachedMode
}

function getServerSnapshot(): LayoutMode {
  return 'desktop'
}

/** 当前布局模式（SSR 首帧恒 desktop，hydration 后按真实视口收敛）。 */
export function useLayoutMode(): LayoutMode {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}

/** 测试/非 React 场景直读。 */
export function currentLayoutMode(): LayoutMode {
  return getSnapshot()
}

/** 布局断点常量集中出口（替代散落的像素字面量）。 */
export function isMobileMode(mode: LayoutMode): boolean {
  return mode === 'mobile'
}
