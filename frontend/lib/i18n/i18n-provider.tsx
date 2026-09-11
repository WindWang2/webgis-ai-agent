'use client'

/**
 * I18n React 上下文（ADR-0144）。
 *
 * 自管 context（而非 next-intl 的 NextIntlClientProvider）：
 * 语言切换 = zustand store 更新 → 本 provider 重渲染 → 整树换 translator，
 * 无路由、无 router.refresh、无全页重载。无 provider 时（单测裸渲染）
 * useT 回落 zh 默认 translator，既有测试零改造。
 */

import React, { createContext, useContext, useEffect, useMemo } from 'react'
import {
  applyLocaleToDom,
  rehydrateLocale,
  useLanguageStore,
} from './language-store'
import type { AppLocale } from './config'
import { defaultTranslator, translatorFor, type TranslateFn } from './translator'

interface I18nContextValue {
  locale: AppLocale
  t: TranslateFn
}

const I18nContext = createContext<I18nContextValue | null>(null)

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const locale = useLanguageStore((s) => s.locale)

  // mount：门控解除 + rehydrate + <html lang>/cookie 兜底同步
  //（正常路径 pre-paint 脚本与 setLocale 已同步，这里是修复极端分歧的兜底）。
  useEffect(() => {
    rehydrateLocale()
  }, [])

  // 语言变化时同步 <html lang>（store setLocale 已做，这里覆盖 rehydrate 路径）。
  useEffect(() => {
    applyLocaleToDom(locale)
  }, [locale])

  const value = useMemo<I18nContextValue>(
    () => ({ locale, t: translatorFor(locale) }),
    [locale]
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

/** 直读 context（测试断言语言用）。 */
export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext)
  // 无 provider（单测裸渲染）：zh 默认 —— 保持既有测试断言中文文案零改造。
  return ctx ?? { locale: 'zh-CN', t: defaultTranslator }
}

export function useI18nOptional(): I18nContextValue | null {
  return useContext(I18nContext)
}
