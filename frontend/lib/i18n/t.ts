/**
 * 命令式 t()（ADR-0144）：给产生文案的**非 React 模块**用
 * （SSE hook 的 toast、lib/map-kit 导出图纸 chrome、lib/mapspec 错误消息…）。
 *
 * 在调用时点读当前 locale —— 事件发生时语言是什么，文案就是什么；
 * 不做已生成消息的回溯翻译（与业界行为一致）。
 *
 * React 组件一律用 useT()（本函数在组件里调用不会随语言切换重渲染）。
 */

import type { AppLocale } from './config'
import { useLanguageStore } from './language-store'
import { translatorFor, type TranslateFn } from './translator'

export function currentLocale(): AppLocale {
  return useLanguageStore.getState().locale
}

export function t(key: string, params?: Record<string, string | number | boolean | Date>): string {
  return translatorFor(currentLocale())(key, params)
}

/** 显式指定语言（describeApiError 之类想跟随用户语言的工具函数偶尔需要）。 */
export function tFor(locale: AppLocale): TranslateFn {
  return translatorFor(locale)
}
