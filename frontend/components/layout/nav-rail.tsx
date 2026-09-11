'use client';

/**
 * NavRail — 主导航竖排图标栏（UI V3 → Workbench V4）。
 *
 * 取代 7 个等宽水平 tab（330px 宽下每个仅 ~47px，标签折行挤压）。
 * - role=tablist + aria-orientation=vertical，roving tabindex；
 * - ArrowUp/Down/Home/End 键盘导航（自动激活）；
 * - 点击 inactive tab → 激活并打开 context panel；点击 active tab → 折叠面板（地图优先）；
 * - 徽标：图层数 / 导出数（无永久 animate-pulse）；
 * - 底部工具区：模板库（drawer，非 tab）+ 面板折叠。
 *
 * Workbench V4（Wave 3）：顶部三模式切换（Explore/Analyze/Compose）——
 * 模式只改变 tab 组合（MODE_TABS 词表），不复制地图状态；每个模式记忆
 * 自己的 active tab；agent 经 set_mode 命令切换时 modeOrigin='agent'，
 * rail 显示一键返回控件（不丢用户上下文）。
 */
import { useCallback, useEffect, useMemo, useRef } from 'react';
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
  ClipboardList,
  Compass,
  FlaskConical,
  PenTool,
  Undo2,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import clsx from 'clsx';
import { useHudStore } from '@/lib/store/useHudStore';
import { useT } from '@/lib/i18n/useT';
import { LayoutDashboard } from 'lucide-react';
import type { LeftTab } from '@/lib/store/hud-types';
import {
  MODE_TABS,
  WORKBENCH_MODES,
  type WorkbenchMode,
} from '@/lib/store/slices/workbenchSlice';

interface RailTabDef {
  key: LeftTab;
  icon: LucideIcon;
}

/** 分组顺序即渲染顺序；null = 分隔线 */
const RAIL_GROUPS: Array<Array<RailTabDef>> = [
  [{ key: 'chat', icon: MessageCircle }],
  [
    { key: 'project', icon: Folder },
    { key: 'data_sources', icon: Database },
    { key: 'layers', icon: Layers },
    { key: 'components', icon: LayoutDashboard },
  ],
  [
    { key: 'analysis', icon: Triangle },
    { key: 'tasks', icon: ListChecks },
    { key: 'results', icon: ClipboardList },
  ],
  [{ key: 'export_layout', icon: Printer }],
];

const RAIL_TABS: RailTabDef[] = RAIL_GROUPS.flat();

const MODE_META: Record<WorkbenchMode, { icon: LucideIcon }> = {
  explore: { icon: Compass },
  analyze: { icon: FlaskConical },
  compose: { icon: PenTool },
};

/** 'exports' tab 无 rail 图标（export_layout 别名），模式词表内过滤。 */
function modeRailTabs(mode: WorkbenchMode): RailTabDef[] {
  const allowed = new Set(MODE_TABS[mode]);
  return RAIL_TABS.filter((tab) => allowed.has(tab.key));
}

export function NavRail() {
  const t = useT('layout');
  const activeTab = useHudStore((s) => s.activeLeftTab);
  const setActiveTab = useHudStore((s) => s.setActiveLeftTab);
  const leftPanelOpen = useHudStore((s) => s.leftPanelOpen);
  const toggleLeftPanel = useHudStore((s) => s.toggleLeftPanel);
  const setTemplatesOpen = useHudStore((s) => s.setTemplatesOpen);
  const layerCount = useHudStore((s) => s.layers.length);
  const exportCount = useHudStore((s) => s.exports.length);
  const resultCount = useHudStore((s) => s.results.length);
  // Review P2 修复：HUD 展开（210px,z-50）会盖住 rail 底部工具区，整体上移避让。
  const hudOpen = useHudStore((s) => s.hudOpen);
  // Workbench V4：模式词表 + agent 切换回执。
  const mode = useHudStore((s) => s.mode);
  const modeOrigin = useHudStore((s) => s.modeOrigin);
  const setWorkbenchMode = useHudStore((s) => s.setWorkbenchMode);

  const badges: Partial<Record<LeftTab, number | undefined>> = {
    layers: layerCount > 0 ? layerCount : undefined,
    export_layout: exportCount > 0 ? exportCount : undefined,
    results: resultCount > 0 ? resultCount : undefined,
  };

  // 模式只改变 tab 组合；'exports' 无 rail 图标，已由 modeRailTabs 过滤。
  // rail 顺序保持 RAIL_GROUPS 稳定序（空间记忆不随模式重排）。
  const visibleTabs = useMemo(() => modeRailTabs(mode), [mode]);

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

  // Review R1（MAJOR-3）：tab 协调已上收 setWorkbenchMode（store 内落
  // activeLeftTab）—— 用户与 agent 路径共享同一协调，rail 不再重复。
  const switchMode = useCallback(
    (next: WorkbenchMode) => {
      if (next === mode) return;
      setWorkbenchMode(next, 'user');
    },
    [mode, setWorkbenchMode],
  );

  /** agent 切换模式的一键返回：恢复 agent 切换前用户所在模式（store 记录）。 */
  const revertAgentMode = useCallback(() => {
    const target = useHudStore.getState().userModeBeforeAgent ?? 'explore';
    setWorkbenchMode(target, 'user');
  }, [setWorkbenchMode]);

  // Review R1（a11y MAJOR-2）：模式切换会卸载不在新词表内的 tab —— 若焦点
  // 在被卸载的 tab 上，activeElement 落到 body。effect 把焦点收回当前
  // active tab。
  const prevModeRef = useRef(mode);
  useEffect(() => {
    if (prevModeRef.current !== mode) {
      prevModeRef.current = mode;
      tabRefs.current.get(activeTab)?.focus();
    }
  }, [mode, activeTab]);

  const onTablistKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const currentIndex = visibleTabs.findIndex((t) => isTabActive(t.key));
      let nextIndex: number | null = null;
      if (e.key === 'ArrowDown') nextIndex = ((currentIndex < 0 ? 0 : currentIndex) + 1) % visibleTabs.length;
      else if (e.key === 'ArrowUp')
        nextIndex = ((currentIndex < 0 ? 0 : currentIndex) - 1 + visibleTabs.length) % visibleTabs.length;
      else if (e.key === 'Home') nextIndex = 0;
      else if (e.key === 'End') nextIndex = visibleTabs.length - 1;
      if (nextIndex === null) return;
      e.preventDefault();
      const next = visibleTabs[nextIndex];
      // 自动激活语义（WAI-APG tabs, activation-on-focus）
      if (!isTabActive(next.key)) setActiveTab(next.key);
      else if (!leftPanelOpen) toggleLeftPanel();
      tabRefs.current.get(next.key)?.focus();
    },
    [visibleTabs, isTabActive, leftPanelOpen, setActiveTab, toggleLeftPanel]
  );

  return (
    <nav
      aria-label={t('nav.main')}
      // V4：宽度/顶距改用 --railW / --topH token；背景改为不透明 surface-panel，
      // 去掉 blur(28px) —— 面板压在持续重绘的地图画布上，backdrop-filter 是最贵
      // 的那一类，而且半透明面板会让底下的地图干扰图标可读性。
      className="fixed left-0 top-topbar z-40 flex w-rail flex-col items-center border-r border-edge-subtle bg-surface-panel"
      style={{
        bottom: hudOpen ? 234 : 24,
        transition: 'bottom 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      {/* Workbench V4：模式切换（只改面板组合，不复制地图状态） */}
      {/* Review R1（a11y MINOR-7）：radiogroup 的必需子元素只能是 radio ——
          agent 回执按钮移出分组容器；roving tabindex + 方向键按 APG radio。 */}
      <div className="flex w-full flex-col items-center gap-1 border-b border-edge-subtle py-2">
        <div role="radiogroup" aria-label={t('nav.workbenchMode')} className="flex w-full flex-col items-center gap-1">
        {WORKBENCH_MODES.map((m) => {
          const ModeIcon = MODE_META[m].icon;
          const modeLabel = t(`modes.${m}`);
          const active = mode === m;
          return (
            <button
              key={m}
              role="radio"
              aria-checked={active}
              tabIndex={active ? 0 : -1}
              aria-label={t('nav.modeSuffix', { name: modeLabel })}
              title={t('nav.modeSuffix', { name: modeLabel })}
              data-testid={`mode-${m}`}
              onClick={() => switchMode(m)}
              className={clsx(
                'relative flex h-9 w-9 items-center justify-center rounded-md transition-colors',
                active
                  ? 'bg-status-accent-soft text-status-accent'
                  : 'text-ink-secondary hover:bg-surface-hover hover:text-ink'
              )}
            >
              {active && (
                <span
                  aria-hidden
                  className="absolute left-[-5px] top-1/2 h-5 w-[2.5px] -translate-y-1/2 rounded-pill bg-status-accent-vivid"
                />
              )}
              <ModeIcon size={16} strokeWidth={active ? 2.1 : 1.6} aria-hidden />
            </button>
          );
        })}
        </div>
        {modeOrigin === 'agent' && (
          <button
            type="button"
            data-testid="mode-agent-revert"
            aria-label={t('nav.agentRevertAria')}
            title={t('nav.agentRevertTitle')}
            onClick={revertAgentMode}
            className="flex h-9 w-9 items-center justify-center rounded-md text-status-warning transition-colors hover:bg-surface-hover"
          >
            <Undo2 size={15} aria-hidden />
          </button>
        )}
      </div>

      <div
        role="tablist"
        aria-label={t('nav.workspacePanels')}
        aria-orientation="vertical"
        onKeyDown={onTablistKeyDown}
        className="flex w-full flex-1 flex-col items-center gap-1 overflow-y-auto py-2"
      >
        {visibleTabs.map(({ key, icon: Icon }) => {
          const label = t(`tabs.${key}`);
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

      {/* 工具区：模板库（drawer）+ 面板折叠 */}
      <div className="flex w-full flex-col items-center gap-1 border-t border-edge-subtle py-2">
        <button
          type="button"
          aria-label={t('nav.templates')}
          title={t('nav.templates')}
          onClick={() => setTemplatesOpen(true)}
          className="flex h-9 w-9 items-center justify-center rounded-md text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
        >
          <LayoutTemplate size={17} strokeWidth={1.6} aria-hidden />
        </button>
        <button
          type="button"
          aria-label={leftPanelOpen ? t('nav.collapsePanel') : t('nav.expandPanel')}
          aria-expanded={leftPanelOpen}
          aria-controls="workspace-panel"
          title={leftPanelOpen ? t('nav.collapsePanel') : t('nav.expandPanel')}
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
