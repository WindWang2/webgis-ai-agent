'use client';

/**
 * Tour 步骤定义（ADR-0147 P6）。
 *
 * targetSelector 指向既有组件的可寻址锚点（优先 aria-label/role 语义选择器，
 * 不要求给业务组件加 data 属性——多线共享面零侵入）。目标缺失（tab 未挂载、
 * 布局差异）时该步降级为居中卡片，tour 永不失焦失败。
 */
export interface TourStep {
  id: string;
  title: string;
  body: string;
  /** 目标元素选择器；缺省/未命中则居中展示。 */
  targetSelector?: string;
}

/** 10 rail tab × 3 工作模式：按模式分站讲解（tab 详情见 nav-rail 分组）。 */
export const TOUR_STEPS: TourStep[] = [
  {
    id: 'welcome',
    title: '欢迎来到 GeoAgent',
    body: '这是一个对话驱动的 GIS 工作台：输入自然语言即可完成数据分析、制图与导出。花 60 秒了解关键入口（随时可按 Esc 跳过，设置里可重看）。',
  },
  {
    id: 'modes',
    title: '三种工作模式',
    body: '左下模式开关切换「探索 / 分析 / 制图」三种模式——每种模式只保留相关 tab：探索（对话·图层·数据）、分析（分析·结果）、制图（组件·制图布局）。',
  },
  {
    id: 'chat',
    title: '对话是主入口',
    body: '在对话面板描述你的分析目标（例如“对这张图做热点分析”），Agent 会规划工具链并在地图上落地结果。',
    targetSelector: '[data-story-message] [role="tabpanel"], [aria-label="对话"]',
  },
  {
    id: 'rail',
    title: 'Rail 功能面板',
    body: '左侧 rail 汇总 10 个功能面板：对话、项目、数据、图层、组件、分析、任务、结果、制图。再点一次当前 tab 可折叠面板。',
    targetSelector: '[role="radiogroup"], nav',
  },
  {
    id: 'palette',
    title: '命令面板（Ctrl+K）',
    body: '任何动作都能从命令面板直达：切换面板、导出、主题……按 Ctrl+K（mac 为 ⌘K）随时唤起；按 ? 查看快捷键总览。',
  },
  {
    id: 'queryConsole',
    title: '高级查询控制台',
    body: '会写 SQL / 过滤表达式？命令面板搜「查询控制台」或在数据面板右上角进入：对目录数据集执行过滤查询、查看下推计划、一键结果上图。',
  },
  {
    id: 'search',
    title: '跨会话搜索',
    body: '命令面板搜「跨会话搜索」：在最近会话的全文（消息/产物/图层名）中查找，命中可直接跳转恢复到对应消息。',
  },
  {
    id: 'history',
    title: '操作历史与撤销',
    body: 'Ctrl+Z / Ctrl+Shift+Z 撤销重做，每步都有 toast 反馈；命令面板搜「操作历史」查看时间线、按图层追溯、一键回退多步。',
  },
  {
    id: 'mapTools',
    title: '地图工具',
    body: '地图工具栏：缩放（+/−）、测距（d）、测面（a）、框选（b）、3D 切换（3）；底图与对比模式在顶栏切换。',
    targetSelector: '[aria-label*="测量" i], [aria-label*="缩放" i]',
  },
  {
    id: 'done',
    title: '开始探索',
    body: '提示卡会陆续介绍进阶玩法（模板库、对比模式、ref 提货券……），全部可在设置 → 系统中重置或重看本引导。',
  },
];

/** 首批高价值提示（每条只出现一次，设置可重置）。 */
export interface Hint {
  id: string;
  text: string;
}

export const HINTS: Hint[] = [
  { id: 'hint-ref-coupon', text: '大结果不会撑爆对话：ref 提货券机制把大数据留在会话数据仓，图层与图表按需取用。' },
  { id: 'hint-command-palette', text: 'Ctrl+K 命令面板可直达任何面板与导出动作，试一下。' },
  { id: 'hint-query-console', text: '会写 SQL？「查询控制台」支持过滤表达式、下推计划披露与结果一键上图。' },
  { id: 'hint-cross-search', text: '「跨会话搜索」能全文检索最近 20 个会话的消息与产物，命中即跳转。' },
  { id: 'hint-comparison', text: '顶栏可开启对比模式（分屏/卷帘），直观比较两版地图。' },
  { id: 'hint-templates', text: '左下「模板」内置制图模板库，一键套用出版级样式。' },
  { id: 'hint-undo-history', text: '「操作历史」面板可按图层追溯样式/显隐/重排，并一键回退多步。' },
  { id: 'hint-story-share', text: '会话可生成 StoryMap 叙事页：章节编排、图表回放、分享卡与叙事 PDF 导出。' },
];
