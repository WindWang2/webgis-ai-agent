'use client';

/**
 * Tour 步骤定义（ADR-0147 P6）。
 *
 * targetSelector 指向既有组件的可寻址锚点（优先 aria-label/role 语义选择器，
 * 不要求给业务组件加 data 属性——多线共享面零侵入）。目标缺失（tab 未挂载、
 * 布局差异）时该步降级为居中卡片，tour 永不失焦失败。
 *
 * i18n（G02）：title/body 存 onboarding namespace 的消息 key（不是文案本身），
 * 渲染处（tour.tsx）经 t() 解析 —— 字段名保留 title/body 是为了兼容既有
 * 消费方（onboarding.test.tsx 的步骤完整性断言读 .title/.body）。
 */
export interface TourStep {
  id: string;
  /** onboarding namespace 下的标题消息 key。 */
  title: string;
  /** onboarding namespace 下的正文消息 key。 */
  body: string;
  /** 目标元素选择器；缺省/未命中则居中展示。 */
  targetSelector?: string;
}

/** 10 rail tab × 3 工作模式：按模式分站讲解（tab 详情见 nav-rail 分组）。 */
export const TOUR_STEPS: TourStep[] = [
  {
    id: 'welcome',
    title: 'steps.welcome.title',
    body: 'steps.welcome.body',
  },
  {
    id: 'modes',
    title: 'steps.modes.title',
    body: 'steps.modes.body',
  },
  {
    id: 'chat',
    title: 'steps.chat.title',
    body: 'steps.chat.body',
    targetSelector: '[data-story-message] [role="tabpanel"], [aria-label="对话"]',
  },
  {
    id: 'rail',
    title: 'steps.rail.title',
    body: 'steps.rail.body',
    targetSelector: '[role="radiogroup"], nav',
  },
  {
    id: 'palette',
    title: 'steps.palette.title',
    body: 'steps.palette.body',
  },
  {
    id: 'queryConsole',
    title: 'steps.queryConsole.title',
    body: 'steps.queryConsole.body',
  },
  {
    id: 'search',
    title: 'steps.search.title',
    body: 'steps.search.body',
  },
  {
    id: 'history',
    title: 'steps.history.title',
    body: 'steps.history.body',
  },
  {
    id: 'mapTools',
    title: 'steps.mapTools.title',
    body: 'steps.mapTools.body',
    targetSelector: '[aria-label*="测量" i], [aria-label*="缩放" i]',
  },
  {
    id: 'done',
    title: 'steps.done.title',
    body: 'steps.done.body',
  },
];

/** 首批高价值提示（每条只出现一次，设置可重置）。 */
export interface Hint {
  id: string;
  /** onboarding namespace 下的提示正文消息 key（渲染处 t() 解析）。 */
  textKey: string;
}

export const HINTS: Hint[] = [
  { id: 'hint-ref-coupon', textKey: 'hints.refCoupon' },
  { id: 'hint-command-palette', textKey: 'hints.commandPalette' },
  { id: 'hint-query-console', textKey: 'hints.queryConsole' },
  { id: 'hint-cross-search', textKey: 'hints.crossSearch' },
  { id: 'hint-comparison', textKey: 'hints.comparison' },
  { id: 'hint-templates', textKey: 'hints.templates' },
  { id: 'hint-undo-history', textKey: 'hints.undoHistory' },
  { id: 'hint-story-share', textKey: 'hints.storyShare' },
];
