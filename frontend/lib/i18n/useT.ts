/**
 * useT / useLocale —— 组件层统一入口（ADR-0144）。
 *
 * 用法：
 *   const t = useT('settings')      // namespace 限定
 *   t('tabs.llm')                   // → settings.tabs.llm
 *   const t = useT()                // 无 namespace，用全键
 *   t('common.close')
 *   const locale = useLocale()      // 'zh-CN' | 'en-US'
 *   const setLocale = useSetLocale()
 */

import { useMemo } from 'react'
import { useI18n } from './i18n-provider'
import { useLanguageStore } from './language-store'
import type { AppLocale } from './config'
import type { TranslateFn } from './translator'

export function useT(namespace?: string): TranslateFn {
  const { t } = useI18n()
  return useMemo(() => {
    if (!namespace) return t
    return (key, params) => t(`${namespace}.${key}`, params)
  }, [t, namespace])
}

export function useLocale(): AppLocale {
  return useI18n().locale
}

export function useSetLocale(): (locale: AppLocale) => void {
  return useLanguageStore((s) => s.setLocale)
}
