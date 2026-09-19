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
import zhDrawers from '@/messages/zh-CN/drawers.json'
import zhStory from '@/messages/zh-CN/story.json'
import zhGeoai from '@/messages/zh-CN/geoai.json'
import zhCommands from '@/messages/zh-CN/commands.json'
import zhCopilot from '@/messages/zh-CN/copilot.json'
import zhCockpit from '@/messages/zh-CN/cockpit.json'
import zhReview from '@/messages/zh-CN/review.json'
import zhConsole from '@/messages/zh-CN/console.json'
import zhExplorer from '@/messages/zh-CN/explorer.json'
import zhWorkbench from '@/messages/zh-CN/workbench.json'
import zhProject from '@/messages/zh-CN/project.json'
import enCommon from '@/messages/en-US/common.json'
import enSettings from '@/messages/en-US/settings.json'
import enErrors from '@/messages/en-US/errors.json'
import enLayout from '@/messages/en-US/layout.json'
import enChat from '@/messages/en-US/chat.json'
import enTweaks from '@/messages/en-US/tweaks.json'
import enSidebar from '@/messages/en-US/sidebar.json'
import enMap from '@/messages/en-US/map.json'
import enDrawers from '@/messages/en-US/drawers.json'
import enStory from '@/messages/en-US/story.json'
import enGeoai from '@/messages/en-US/geoai.json'
import enCommands from '@/messages/en-US/commands.json'
import enCopilot from '@/messages/en-US/copilot.json'
import enCockpit from '@/messages/en-US/cockpit.json'
import enReview from '@/messages/en-US/review.json'
import enConsole from '@/messages/en-US/console.json'
import enExplorer from '@/messages/en-US/explorer.json'
import enWorkbench from '@/messages/en-US/workbench.json'
import enProject from '@/messages/en-US/project.json'
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
  drawers: typeof zhDrawers
  story: typeof zhStory
  geoai: typeof zhGeoai
  commands: typeof zhCommands
  copilot: typeof zhCopilot
  cockpit: typeof zhCockpit
  review: typeof zhReview
  console: typeof zhConsole
  explorer: typeof zhExplorer
  workbench: typeof zhWorkbench
  project: typeof zhProject
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
    drawers: zhDrawers,
    story: zhStory,
    geoai: zhGeoai,
    commands: zhCommands,
    copilot: zhCopilot,
    cockpit: zhCockpit,
    review: zhReview,
    console: zhConsole,
    explorer: zhExplorer,
    workbench: zhWorkbench,
    project: zhProject,
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
    drawers: enDrawers as unknown as AppMessages['drawers'],
    story: enStory as unknown as AppMessages['story'],
    geoai: enGeoai as unknown as AppMessages['geoai'],
    commands: enCommands as unknown as AppMessages['commands'],
    copilot: enCopilot as unknown as AppMessages['copilot'],
    cockpit: enCockpit as unknown as AppMessages['cockpit'],
    review: enReview as unknown as AppMessages['review'],
    console: enConsole as unknown as AppMessages['console'],
    explorer: enExplorer as unknown as AppMessages['explorer'],
    workbench: enWorkbench as unknown as AppMessages['workbench'],
    project: enProject as unknown as AppMessages['project'],
  },
}
