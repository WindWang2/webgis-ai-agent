'use client';

import React, { useCallback, useRef } from 'react';
import {
  Sparkles,
  Hash,
  Brain,
  Crosshair,
  Settings,
  UserRound,
  X,
  ShieldCheck,
  Layers,
} from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { LlmConfig } from './llm-config';
import { SkillsHub } from './skills-hub';
import { RagConfig } from './rag-config';
import { MapConfig } from './map-config';
import { LayerManagement } from './layer-management';
import { SystemSettings } from './system-settings';
import { AccountSection } from './account-section';

/* ------------------------------------------------------------------ */
/*  Nav item definition                                                */
/* ------------------------------------------------------------------ */

interface NavItem {
  key: 'llm' | 'skills' | 'rag' | 'map' | 'layers' | 'system' | 'account';
  label: string;
  icon: React.ElementType;
  count?: number;
}

const NAV_ITEMS: NavItem[] = [
  { key: 'llm', label: '大模型', icon: Sparkles },
  { key: 'skills', label: 'Skills', icon: Hash, count: 0 },
  { key: 'rag', label: '知识库', icon: Brain },
  { key: 'map', label: '地图配置', icon: Crosshair },
  // U-1（#883）：图层管理面板此前完整实现却无导航入口（不可达死代码）。
  { key: 'layers', label: '图层', icon: Layers },
  { key: 'system', label: '系统', icon: Settings },
  { key: 'account', label: '账户', icon: UserRound },
];

/* ------------------------------------------------------------------ */
/*  Tab content components                                             */
/* ------------------------------------------------------------------ */

function TabContent({ tab }: { tab: string }) {
  switch (tab) {
    case 'llm':
      return <LlmConfig />;
    case 'skills':
      return <SkillsHub />;
    case 'rag':
      return <RagConfig />;
    case 'map':
      return <MapConfig />;
    case 'layers':
      return <LayerManagement />;
    case 'system':
      return <SystemSettings />;
    case 'account':
      return <AccountSection />;
    default:
      return null;
  }
}

/* ------------------------------------------------------------------ */
/*  Settings Panel                                                     */
/* ------------------------------------------------------------------ */

export function SettingsPanel() {
  const settingsOpen = useHudStore((s) => s.settingsOpen);
  const setSettingsOpen = useHudStore((s) => s.setSettingsOpen);
  const settingsTab = useHudStore((s) => s.settingsTab);
  const setSettingsTab = useHudStore((s) => s.setSettingsTab);

  const skills = useHudStore((s) => s.skills);

  const drawerRef = useRef<HTMLDivElement | null>(null);
  const tabRefs = useRef<Map<string, HTMLButtonElement>>(new Map());

  const close = useCallback(() => setSettingsOpen(false), [setSettingsOpen]);

  // UI V3 dialog 焦点管理（共用 hook）：初始聚焦 / 焦点归还 / document 级
  // Tab 围栏 + Escape。
  useDialogFocus({
    open: settingsOpen,
    containerRef: drawerRef,
    onEscape: close,
    initialFocusSelector: '[role="tab"]',
  });

  // Review P1 修复：tablist 补 WAI-APG 方向键导航（roving tabindex 已存在，
  // 之前缺 ArrowUp/Down/Home/End，键盘用户无法切换设置分类）。
  const onNavKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const idx = NAV_ITEMS.findIndex((item) => item.key === settingsTab);
      let next: number | null = null;
      if (e.key === 'ArrowDown') next = ((idx < 0 ? 0 : idx) + 1) % NAV_ITEMS.length;
      else if (e.key === 'ArrowUp')
        next = ((idx < 0 ? 0 : idx) - 1 + NAV_ITEMS.length) % NAV_ITEMS.length;
      else if (e.key === 'Home') next = 0;
      else if (e.key === 'End') next = NAV_ITEMS.length - 1;
      if (next === null) return;
      e.preventDefault();
      const key = NAV_ITEMS[next].key;
      setSettingsTab(key);
      tabRefs.current.get(key)?.focus();
    },
    [settingsTab, setSettingsTab]
  );

  if (!settingsOpen) return null;

  // #551：skills[].enabled 假开关已移除（无消费方）—— 徽标改为真实技能总数。
  const skillCount = skills.length;

  const navWithCounts: NavItem[] = NAV_ITEMS.map((item) => {
    if (item.key === 'skills') return { ...item, count: skillCount };
    return item;
  });

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[100] bg-surface-scrim"
        onClick={() => setSettingsOpen(false)}
      />

      {/* Drawer */}
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-panel-title"
        tabIndex={-1}
        /* 宽度走 --drawer-w（见 globals.css）：约束是"地图仍然可见"，
           两个右侧抽屉共用同一条规则。 */
        className="fixed inset-y-0 right-0 z-[101] flex animate-slide-from-right"
        style={{ width: 'var(--drawer-w)' }}
      >
        {/* E: 抽屉本体之前有两处 backdrop-filter: blur(32px)（左栏 + 内容区）。
            backdrop 压在持续重绘的地图画布上是最贵的那类合成，而且 scrim 已经
            盖住地图 —— 全部移除，改不透明 surface-panel + shadow-drawer。 */}
        {/* Left nav rail */}
        <nav
          aria-label="设置分类"
          className="flex flex-col border-r border-edge-subtle bg-surface-panel py-4"
          style={{
            width: 136,
            flexShrink: 0,
          }}
        >
          {/* Header mini */}
          <div className="px-4 mb-4">
            <div className="flex items-center gap-1.5">
              <ShieldCheck size={14} className="text-agent-accent" />
              <span className="text-title font-semibold text-ink">控制中心</span>
            </div>
          </div>

          {/* Nav items */}
          <div
            role="tablist"
            aria-orientation="vertical"
            aria-label="设置分类"
            onKeyDown={onNavKeyDown}
            className="flex flex-col gap-0.5 px-2 flex-1"
          >
            {navWithCounts.map((item) => {
              const Icon = item.icon;
              const isActive = settingsTab === item.key;
              return (
                /* 选中项的 label/icon 是 accent 作文字，暗色下 2.96–3.40:1 ——
                   改用主题校正后的 --agent-accent；底色同源，
                   --agent-accent。 */
                <button
                    key={item.key}
                    ref={(el) => {
                      if (el) tabRefs.current.set(item.key, el);
                      else tabRefs.current.delete(item.key);
                    }}
                    role="tab"
                    id={`settings-tab-${item.key}`}
                    aria-selected={isActive}
                    aria-controls="settings-tabpanel"
                    tabIndex={isActive ? 0 : -1}
                    onClick={() => setSettingsTab(item.key)}
                    className="flex items-center gap-2 rounded-md px-2.5 py-2 text-left transition-all duration-150"
                    style={{
                      backgroundColor: isActive
                        ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 10%, transparent)'
                        : 'transparent',
                      color: isActive ? 'var(--agent-accent)' : 'var(--text-secondary)',
                    }}
                  >
                    <Icon
                      size={16}
                      style={{
                        color: isActive ? 'var(--agent-accent)' : 'var(--text-muted)',
                      }}
                    />
                    <span
                      className="text-body font-medium truncate flex-1"
                      style={{ color: isActive ? 'var(--agent-accent)' : 'var(--text-secondary)' }}
                    >
                      {item.label}
                    </span>
                    {item.count !== undefined && item.count > 0 && (
                      <span
                        className="text-caption font-bold rounded-pill px-1.5 leading-tight"
                        style={{
                          backgroundColor: isActive
                            ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 14%, transparent)'
                            : 'var(--surface-sunken)',
                          color: isActive ? 'var(--agent-accent)' : 'var(--text-muted)',
                          minWidth: 18,
                          textAlign: 'center',
                        }}
                      >
                        {item.count}
                      </span>
                    )}
                  </button>
              );
            })}
          </div>
        </nav>

        {/* Right content area */}
        <div className="flex flex-1 flex-col bg-surface-panel shadow-drawer">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-edge-subtle px-6 py-4">
            <div className="flex items-center gap-3">
              <div
                className="flex h-9 w-9 items-center justify-center rounded-md"
                style={{
                  background:
                    'linear-gradient(135deg, var(--agent-accent, #16a34a) 0%, color-mix(in srgb, var(--agent-accent, #16a34a) 72%, #ffffff) 100%)',
                }}
              >
                <Settings size={18} className="text-ink-on-accent" />
              </div>
              <div>
                <div id="settings-panel-title" className="text-title font-bold leading-tight text-ink">
                  Agent 控制中心
                </div>
                <div className="text-meta leading-tight text-ink-muted">Agent Command Center</div>
              </div>
              <span
                className="ml-2 inline-flex items-center gap-1 rounded-pill px-2 py-0.5 text-meta font-medium"
                style={{
                  backgroundColor: 'color-mix(in srgb, var(--agent-accent, #16a34a) 10%, transparent)',
                  color: 'var(--agent-accent)',
                }}
              >
                <span
                  className="inline-block h-1.5 w-1.5 rounded-full"
                  style={{ backgroundColor: 'var(--agent-accent, #16a34a)' }}
                />
                系统在线
              </span>
            </div>

            <button
              onClick={() => setSettingsOpen(false)}
              aria-label="关闭设置"
              className="flex h-8 w-8 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-surface-hover"
            >
              <X size={18} />
            </button>
          </div>

          {/* Tab content */}
          <div
            role="tabpanel"
            id="settings-tabpanel"
            aria-labelledby={`settings-tab-${settingsTab}`}
            className="flex-1 overflow-y-auto px-6 py-5"
          >
            <TabContent tab={settingsTab} />
          </div>
        </div>
      </div>
    </>
  );
}
