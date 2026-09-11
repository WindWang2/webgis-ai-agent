/**
 * 消息 catalog 聚合（ADR-0144）。
 *
 * 静态 import 全部 locale × namespace（catalog 是小体积 JSON，静态打包换取：
 * 同步可读 —— pre-paint / 命令式 t() / SSR 都不依赖异步加载；TS 可校验）。
 * 新增 namespace：在 messages/<locale>/ 下建 <ns>.json 并在此登记一次。
 *
 * 键结构：messages['zh-CN'].common.close —— translator 以 'common.close' 点路径取值。
 */

import zhCommon from '@/messages/zh-CN/common.json'
import zhSettings from '@/messages/zh-CN/settings.json'
import zhErrors from '@/messages/zh-CN/errors.json'
import zhLayout from '@/messages/zh-CN/layout.json'
import zhChat from '@/messages/zh-CN/chat.json'
import zhTweaks from '@/messages/zh-CN/tweaks.json'
import zhSidebar from '@/messages/zh-CN/sidebar.json'
import zhMap from '@/messages/zh-CN/map.json'
import enCommon from '@/messages/en-US/common.json'
import enSettings from '@/messages/en-US/settings.json'
import enErrors from '@/messages/en-US/errors.json'
import enLayout from '@/messages/en-US/layout.json'
import enChat from '@/messages/en-US/chat.json'
import enTweaks from '@/messages/en-US/tweaks.json'
import enSidebar from '@/messages/en-US/sidebar.json'
import enMap from '@/messages/en-US/map.json'
import type { AppLocale } from './config'

export interface AppMessages {
  common: typeof zhCommon
  settings: typeof zhSettings
  errors: typeof zhErrors
  layout: typeof zhLayout
  chat: typeof zhChat
  tweaks: typeof zhTweaks
  sidebar: typeof zhSidebar
  map: typeof zhMap
}

export const messages: Record<AppLocale, AppMessages> = {
  'zh-CN': {
    common: zhCommon,
    settings: zhSettings,
    errors: zhErrors,
    layout: zhLayout,
    chat: zhChat,
    tweaks: zhTweaks,
    sidebar: zhSidebar,
    map: zhMap,
  },
  'en-US': {
    // en 与 zh 的 catalog 结构由 test/i18n/key-completeness.test.ts 强制一致。
    common: enCommon as unknown as AppMessages['common'],
    settings: enSettings as unknown as AppMessages['settings'],
    errors: enErrors as unknown as AppMessages['errors'],
    layout: enLayout as unknown as AppMessages['layout'],
    chat: enChat as unknown as AppMessages['chat'],
    tweaks: enTweaks as unknown as AppMessages['tweaks'],
    sidebar: enSidebar as unknown as AppMessages['sidebar'],
    map: enMap as unknown as AppMessages['map'],
  },
}
