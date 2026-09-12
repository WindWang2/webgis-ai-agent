/**
 * i18n 基础配置（ADR-0144）。
 *
 * 本应用是单 URL 工作台（无 [locale] 路由段）：语言是用户设置而非路由属性，
 * 因此不走 next-intl 的 i18n routing，只取其 ICU 消息格式化引擎
 * （createTranslator），上下文与状态由本目录的 store/provider 管理。
 *
 * 无处不在的三个一致性问题都在这里钉死：
 * 1. localStorage 键（zustand persist，供 pre-paint 之外的运行时读取）
 * 2. cookie 名（layout.tsx 服务端读它决定 SSR 的 <html lang> 与首帧语言 ——
 *    这是「刷新无闪白」的唯一来源；改 cookie 名必须同步 layout.tsx）
 * 3. window 全局名（pre-paint 内联脚本把 cookie 里的语言写入它，
 *    store 首次渲染同步读它 —— 保证 hydration 首帧与 SSR 一致，无 mismatch）
 */

export const SUPPORTED_LOCALES = ['zh-CN', 'en-US'] as const

export type AppLocale = (typeof SUPPORTED_LOCALES)[number]

export const DEFAULT_LOCALE: AppLocale = 'zh-CN'

export const LOCALE_COOKIE = 'geoagent-locale'

/** zustand persist 的 localStorage 键（独立于 hud 的 geoagent-settings，避免双 store 互踩）。 */
export const LOCALE_STORAGE_KEY = 'geoagent-locale'

/** pre-paint 脚本写入 window 的语言全局（layout.tsx 内联脚本与本 store 共用常量）。 */
export const LOCALE_GLOBAL_VAR = '__GEOAGENT_LOCALE__'

export function isAppLocale(value: unknown): value is AppLocale {
  return (
    typeof value === 'string' &&
    (SUPPORTED_LOCALES as readonly string[]).includes(value)
  )
}

/** BCP-47 宽松归一：zh* → zh-CN，en* → en-US，其余回落默认语言。 */
export function normalizeLocale(value: string | null | undefined): AppLocale {
  if (!value) return DEFAULT_LOCALE
  const lower = value.toLowerCase()
  if (lower.startsWith('zh')) return 'zh-CN'
  if (lower.startsWith('en')) return 'en-US'
  return DEFAULT_LOCALE
}

/** 语言切换器上的自称标签（始终以该语言自身书写，不翻译）。 */
export const LOCALE_LABELS: Record<AppLocale, string> = {
  'zh-CN': '中文',
  'en-US': 'English',
}
