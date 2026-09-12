/**
 * 翻译函数层（ADR-0144）。
 *
 * 用 next-intl 的 createTranslator 作为 ICU 消息格式化引擎（{var} 占位、
 * 复数/选择等 ICU 语法），但不使用其 React context（useTranslations）——
 * 上下文由 i18n-provider.tsx 自管，好处：
 * 1. 语言是客户端设置而非路由，provider 可随 store 即时切换；
 * 2. 无 provider 时（单测裸渲染）回落到 zh 默认 translator，
 *    既有测试无需包 wrapper 即可继续断言中文文案；
 * 3. 命令式 t()（lib 逻辑层产生 toast/消息）与 React 渲染共用同一 catalog。
 */

import { createTranslator, IntlErrorCode } from 'next-intl'
import { DEFAULT_LOCALE, type AppLocale } from './config'
import { messages } from './messages'

/** 对外暴露的翻译函数签名（点路径键 + ICU 值）。 */
export type TranslateFn = (
  key: string,
  params?: Record<string, string | number | boolean | Date>
) => string

const translatorCache = new Map<AppLocale, TranslateFn>()

function onError(error: unknown): void {
  // 缺 key 静默回落（getMessageFallback 返回键路径），不刷屏；
  // 缺 key 本身由 test/i18n/key-completeness.test.ts 在 CI 兜住。
  if (
    error &&
    typeof error === 'object' &&
    'code' in error &&
    (error as { code?: unknown }).code === IntlErrorCode.MISSING_MESSAGE
  ) {
    return
  }
  console.error('[i18n]', error)
}

/** 取指定语言的 translator（缓存；ICU formatter 按语言复用）。 */
export function translatorFor(locale: AppLocale): TranslateFn {
  const cached = translatorCache.get(locale)
  if (cached) return cached
  const translator = createTranslator({
    locale,
    messages: messages[locale],
    onError,
  }) as unknown as TranslateFn
  translatorCache.set(locale, translator)
  return translator
}

/** 无 provider 场景的回落 translator（zh-CN）：单测裸渲染、SSR 早期。 */
export const defaultTranslator: TranslateFn = translatorFor(DEFAULT_LOCALE)
