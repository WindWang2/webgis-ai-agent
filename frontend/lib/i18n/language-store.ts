/**
 * 语言状态 store（ADR-0144）。
 *
 * 三份真相、一条同步链（谁在读决定谁是权威）：
 * - zustand persist（localStorage 'geoagent-locale'）：运行时持久化；
 * - cookie 'geoagent-locale'：layout.tsx 服务端读它出 SSR 语言（无闪白的关键）；
 * - window.__GEOAGENT_LOCALE__：pre-paint 内联脚本从 cookie 写入，
 *   store 创建时同步读取 —— 保证 hydration 首帧与 SSR 一致。
 *
 * 与 useHudStore 相同的 skipHydration + 门控写模式：mount 前任何 set()
 * 都可能把内存默认值刷进 storage 抹掉用户选择；ClientProviders mount 后
 * 调 enableLocalePersistWrites() 再 rehydrate()。
 */

import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import {
  DEFAULT_LOCALE,
  isAppLocale,
  LOCALE_COOKIE,
  LOCALE_GLOBAL_VAR,
  LOCALE_STORAGE_KEY,
  type AppLocale,
} from './config'

interface LanguageState {
  locale: AppLocale
  setLocale: (locale: AppLocale) => void
}

/** pre-paint 内联脚本写入的全局（layout.tsx 与本文件共用 LOCALE_GLOBAL_VAR）。 */
function readLocaleFromGlobal(): AppLocale {
  if (typeof window === 'undefined') return DEFAULT_LOCALE
  try {
    const value = (window as unknown as Record<string, unknown>)[LOCALE_GLOBAL_VAR]
    return isAppLocale(value) ? value : DEFAULT_LOCALE
  } catch {
    return DEFAULT_LOCALE
  }
}

export function applyLocaleToDom(locale: AppLocale): void {
  if (typeof document === 'undefined') return
  document.documentElement.lang = locale
}

export function writeLocaleCookie(locale: AppLocale): void {
  if (typeof document === 'undefined') return
  document.cookie = `${LOCALE_COOKIE}=${locale}; path=/; max-age=31536000; samesite=lax`
}

let persistWritesEnabled =
  typeof process !== 'undefined' && !!process.env.VITEST

export function enableLocalePersistWrites(): void {
  persistWritesEnabled = true
}

function readBrowserStorage(): Storage | null {
  try {
    if (typeof window === 'undefined') return null
    return window.localStorage
  } catch {
    return null
  }
}

const gatedStorage = {
  getItem: (name: string) => readBrowserStorage()?.getItem(name) ?? null,
  setItem: (name: string, value: string) => {
    if (!persistWritesEnabled) return
    readBrowserStorage()?.setItem(name, value)
  },
  removeItem: (name: string) => {
    readBrowserStorage()?.removeItem(name)
  },
}

export const useLanguageStore = create<LanguageState>()(
  persist(
    (set) => ({
      locale: readLocaleFromGlobal(),
      setLocale: (locale) => {
        set({ locale })
        // DOM / cookie 同步即时做：<html lang> 影响屏幕阅读器与字体选择，
        // cookie 保证下次 SSR 首帧就是该语言（无闪白）。
        applyLocaleToDom(locale)
        writeLocaleCookie(locale)
      },
    }),
    {
      name: LOCALE_STORAGE_KEY,
      skipHydration: true,
      storage: createJSONStorage(() => gatedStorage),
      partialize: (state) => ({ locale: state.locale }),
    }
  )
)

/**
 * mount 后由 I18nProvider 调用：rehydrate 持久化值并补齐 DOM/cookie。
 *
 * 持久化值与 SSR 值（cookie 诱导的初始值）不一致时以持久化值为准：
 * 浏览器策略清 cookie 后 localStorage 仍是用户意图 —— 这发生在 mount 之后的
 * setState，是合法的 post-hydration 更新，不产生 hydration mismatch；
 * 代价是该极端场景首帧闪一次旧语言（可接受，且下一次刷新即有 cookie）。
 */
export function rehydrateLocale(): void {
  enableLocalePersistWrites()
  void Promise.resolve(useLanguageStore.persist.rehydrate()).finally(() => {
    const locale = useLanguageStore.getState().locale
    applyLocaleToDom(locale)
    writeLocaleCookie(locale)
  })
}
