'use client';

/**
 * NavRail — 主导航竖排图标栏（UI V3）。
 *
 * 取代 7 个等宽水平 tab（330px 宽下每个仅 ~47px，标签折行挤压）。
 * - role=tablist + aria-orientation=vertical，roving tabindex；
 * - ArrowUp/Down/Home/End 键盘导航（自动激活）；
 * - 点击 inactive tab → 激活并打开 context panel；点击 active tab → 折叠面板（地图优先）；
 * - 徽标：图层数 / 导出数（无永久 animate-pulse）；
 * - 底部工具区：模板库（drawer，非 tab）+ 面板折叠。
 */
import { useCallback, useRef } from 'react';
import {
  MessageCircle,
  Folder,
  Database,
  Layers,
  Triangle,
  ListChecks,
  Printer,
  LayoutTemplate,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import clsx from 'clsx';
import { useHudStore } from '@/lib/store/useHudStore';
import type { LeftTab } from '@/lib/store/hud-types';

interface RailTabDef {
  key: LeftTab;
  icon: LucideIcon;
  label: string;
}

/** 分组顺序即渲染顺序；null = 分隔线 */
const RAIL_GROUPS: Array<Array<RailTabDef>> = [
  [{ key: 'chat', icon: MessageCircle, label: '对话' }],
  [
    { key: 'project', icon: Folder, label: '项目' },
    { key: 'data_sources', icon: Database, label: '数据' },
    { key: 'layers', icon: Layers, label: '图层' },
  ],
  [
    { key: 'analysis', icon: Triangle, label: '分析' },
    { key: 'tasks', icon: ListChecks, label: '任务' },
  ],
  [{ key: 'export_layout', icon: Printer, label: '制图' }],
];

const RAIL_TABS: RailTabDef[] = RAIL_GROUPS.flat();

export function NavRail() {
  const activeTab = useHudStore((s) => s.activeLeftTab);
  const setActiveTab = useHudStore((s) => s.setActiveLeftTab);
  const leftPanelOpen = useHudStore((s) => s.leftPanelOpen);
  const toggleLeftPanel = useHudStore((s) => s.toggleLeftPanel);
  const setTemplatesOpen = useHudStore((s) => s.setTemplatesOpen);
  const layerCount = useHudStore((s) => s.layers.length);
  const exportCount = useHudStore((s) => s.exports.length);
  // Review P2 修复：HUD 展开（210px,z-50）会盖住 rail 底部工具区，整体上移避让。
  const hudOpen = useHudStore((s) => s.hudOpen);

  const badges: Partial<Record<LeftTab, number | undefined>> = {
    layers: layerCount > 0 ? layerCount : undefined,
    export_layout: exportCount > 0 ? exportCount : undefined,
  };

  const tabRefs = useRef<Map<string, HTMLButtonElement>>(new Map());

  const isTabActive = useCallback(
    (key: LeftTab) =>
      activeTab === key || (key === 'export_layout' && activeTab === 'exports'),
    [activeTab]
  );

  const activateTab = useCallback(
    (key: LeftTab) => {
      if (isTabActive(key)) {
        // 点击当前 tab → 折叠/展开面板（地图优先）
        toggleLeftPanel();
      } else {
        setActiveTab(key); // store：切 tab 即打开面板
      }
    },
    [isTabActive, setActiveTab, toggleLeftPanel]
  );

  const onTablistKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const currentIndex = RAIL_TABS.findIndex((t) => isTabActive(t.key));
      let nextIndex: number | null = null;
      if (e.key === 'ArrowDown') nextIndex = ((currentIndex < 0 ? 0 : currentIndex) + 1) % RAIL_TABS.length;
      else if (e.key === 'ArrowUp')
        nextIndex = ((currentIndex < 0 ? 0 : currentIndex) - 1 + RAIL_TABS.length) % RAIL_TABS.length;
      else if (e.key === 'Home') nextIndex = 0;
      else if (e.key === 'End') nextIndex = RAIL_TABS.length - 1;
      if (nextIndex === null) return;
      e.preventDefault();
      const next = RAIL_TABS[nextIndex];
      // 自动激活语义（WAI-APG tabs, activation-on-focus）
      if (!isTabActive(next.key)) setActiveTab(next.key);
      else if (!leftPanelOpen) toggleLeftPanel();
      tabRefs.current.get(next.key)?.focus();
    },
    [isTabActive, leftPanelOpen, setActiveTab, toggleLeftPanel]
  );

  return (
    <nav
      aria-label="主导航"
      // V4：宽度/顶距改用 --railW / --topH token；背景改为不透明 surface-panel，
      // 去掉 blur(28px) —— 面板压在持续重绘的地图画布上，backdrop-filter 是最贵
      // 的那一类，而且半透明面板会让底下的地图干扰图标可读性。
      className="fixed left-0 top-topbar z-40 flex w-rail flex-col items-center border-r border-edge-subtle bg-surface-panel"
      style={{
        bottom: hudOpen ? 234 : 24,
        transition: 'bottom 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      <div
        role="tablist"
        aria-label="工作区面板"
        aria-orientation="vertical"
        onKeyDown={onTablistKeyDown}
        className="flex w-full flex-1 flex-col items-center gap-1 overflow-y-auto py-2"
      >
        {RAIL_GROUPS.map((group, gi) => (
          <div key={gi} className="flex w-full flex-col items-center gap-1">
            {gi > 0 && <div aria-hidden className="my-1 w-6 border-t border-edge-subtle" />}
            {group.map(({ key, icon: Icon, label }) => {
              const active = isTabActive(key);
              const badge = badges[key];
              return (
                <button
                  key={key}
                  ref={(el) => {
                    if (el) tabRefs.current.set(key, el);
                    else tabRefs.current.delete(key);
                  }}
                  role="tab"
                  id={`rail-tab-${key}`}
                  // aria-selected 只表达“当前 tab”（APG）；面板开合由 panel
                  // 自身 aria-hidden 与折叠按钮 aria-expanded 传达。
                  aria-selected={active}
                  aria-controls="workspace-panel"
                  aria-label={label}
                  title={label}
                  tabIndex={active ? 0 : -1}
                  onClick={() => activateTab(key)}
                  // 审计修复：active 用的底色类与 hover 完全相同，于是
                  // hover 当前 tab 时毫无反馈，"已选中" 与 "指针在上面" 视觉同源。
                  // 现在 selected = accent 软底 + accent 图标 + 左侧指示条，
                  // hover 只是中性底色，两者不再混淆。
                  className={clsx(
                    'relative flex h-9 w-9 items-center justify-center rounded-md transition-colors',
                    active
                      ? 'bg-status-accent-soft text-status-accent'
                      : 'text-ink-secondary hover:bg-surface-hover hover:text-ink'
                  )}
                >
                  {active && leftPanelOpen && (
                    <span
                      aria-hidden
                      className="absolute left-[-5px] top-1/2 h-5 w-[2.5px] -translate-y-1/2 rounded-pill bg-status-accent-vivid"
                    />
                  )}
                  <Icon size={17} strokeWidth={active ? 2.1 : 1.6} aria-hidden />
                  {badge !== undefined && (
                    <span
                      aria-hidden
                      // 计数徽标是中性信息，不是交互重点 —— 之前用满饱和品牌绿，
                      // 与 active 指示条抢同一个视觉权重。
                      className="absolute right-0 top-0 inline-flex h-3.5 min-w-[14px] items-center justify-center rounded-pill bg-surface-sunken px-0.5 text-micro font-semibold tabular-nums text-ink-secondary ring-1 ring-edge-subtle"
                    >
                      {badge}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {/* 工具区：模板库（drawer）+ 面板折叠 */}
      <div className="flex w-full flex-col items-center gap-1 border-t border-edge-subtle py-2">
        <button
          type="button"
          aria-label="模板库"
          title="模板库"
          onClick={() => setTemplatesOpen(true)}
          className="flex h-9 w-9 items-center justify-center rounded-md text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
        >
          <LayoutTemplate size={17} strokeWidth={1.6} aria-hidden />
        </button>
        <button
          type="button"
          aria-label={leftPanelOpen ? '折叠面板' : '展开面板'}
          aria-expanded={leftPanelOpen}
          aria-controls="workspace-panel"
          title={leftPanelOpen ? '折叠面板' : '展开面板'}
          onClick={toggleLeftPanel}
          className="flex h-9 w-9 items-center justify-center rounded-md text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
        >
          {leftPanelOpen ? (
            <PanelLeftClose size={17} strokeWidth={1.6} aria-hidden />
          ) : (
            <PanelLeftOpen size={17} strokeWidth={1.6} aria-hidden />
          )}
        </button>
      </div>
    </nav>
  );
}

export default NavRail;
