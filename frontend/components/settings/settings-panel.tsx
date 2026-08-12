'use client';

import React, { useCallback, useEffect, useRef } from 'react';
import {
  Sparkles,
  Hash,
  Brain,
  Crosshair,
  Settings,
  X,
  ShieldCheck,
} from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { trapTabKey } from '@/lib/utils/focus';
import { LlmConfig } from './llm-config';
import { SkillsHub } from './skills-hub';
import { RagConfig } from './rag-config';
import { MapConfig } from './map-config';
import { SystemSettings } from './system-settings';

/* ------------------------------------------------------------------ */
/*  Nav item definition                                                */
/* ------------------------------------------------------------------ */

interface NavItem {
  key: 'llm' | 'skills' | 'rag' | 'map' | 'system';
  label: string;
  icon: React.ElementType;
  count?: number;
}

const NAV_ITEMS: NavItem[] = [
  { key: 'llm', label: '大模型', icon: Sparkles },
  { key: 'skills', label: 'Skills', icon: Hash, count: 0 },
  { key: 'rag', label: '知识库', icon: Brain },
  { key: 'map', label: '地图配置', icon: Crosshair },
  { key: 'system', label: '系统', icon: Settings },
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
    case 'system':
      return <SystemSettings />;
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
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!settingsOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSettingsOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [settingsOpen, setSettingsOpen]);

  // UI V3 dialog 焦点管理：打开时聚焦第一个导航项，关闭时归还触发元素。
  useEffect(() => {
    if (!settingsOpen) return;
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    const t = setTimeout(() => {
      drawerRef.current?.querySelector<HTMLElement>('[role="tab"]')?.focus();
    }, 50);
    return () => {
      clearTimeout(t);
      restoreFocusRef.current?.focus?.();
      restoreFocusRef.current = null;
    };
  }, [settingsOpen]);

  const onDrawerKeyDown = useCallback((e: React.KeyboardEvent) => {
    trapTabKey(e.nativeEvent, drawerRef.current);
  }, []);

  if (!settingsOpen) return null;

  const enabledSkillCount = skills.filter((s) => s.enabled).length;

  const navWithCounts: NavItem[] = NAV_ITEMS.map((item) => {
    if (item.key === 'skills') return { ...item, count: enabledSkillCount };
    return item;
  });

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[100] bg-slate-900/20 backdrop-blur-sm"
        onClick={() => setSettingsOpen(false)}
      />

      {/* Drawer */}
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-panel-title"
        onKeyDown={onDrawerKeyDown}
        className="fixed inset-y-0 right-0 z-[101] flex animate-slide-from-right"
        style={{ width: 'min(720px, 92vw)' }}
      >
        {/* Left nav rail */}
        <nav
          aria-label="设置分类"
          className="flex flex-col border-r py-4"
          style={{
            width: 136,
            flexShrink: 0,
            borderColor: 'var(--theme-border)',
            background: 'var(--theme-bg-panel)',
            backdropFilter: 'blur(32px)',
            WebkitBackdropFilter: 'blur(32px)',
          }}
        >
          {/* Header mini */}
          <div className="px-4 mb-4">
            <div className="flex items-center gap-1.5">
              <ShieldCheck size={14} style={{ color: 'var(--agent-accent, #16a34a)' }} />
              <span className="text-[14px] font-semibold" style={{ color: 'var(--theme-text-primary)' }}>
                控制中心
              </span>
            </div>
          </div>

          {/* Nav items */}
          <div role="tablist" aria-orientation="vertical" className="flex flex-col gap-0.5 px-2 flex-1">
            {navWithCounts.map((item) => {
              const Icon = item.icon;
              const isActive = settingsTab === item.key;
              return (
                <button
                  key={item.key}
                  role="tab"
                  id={`settings-tab-${item.key}`}
                  aria-selected={isActive}
                  aria-controls="settings-tabpanel"
                  tabIndex={isActive ? 0 : -1}
                  onClick={() => setSettingsTab(item.key)}
                  className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-left transition-all duration-150"
                  style={{
                    backgroundColor: isActive
                      ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 10%, transparent)'
                      : 'transparent',
                    color: isActive ? 'var(--agent-accent, #16a34a)' : 'var(--theme-text-secondary)',
                  }}
                >
                  <Icon
                    size={16}
                    style={{
                      color: isActive ? 'var(--agent-accent, #16a34a)' : 'var(--theme-text-muted)',
                    }}
                  />
                  <span
                    className="text-[13px] font-medium truncate flex-1"
                    style={{ color: isActive ? 'var(--agent-accent, #16a34a)' : 'var(--theme-text-secondary)' }}
                  >
                    {item.label}
                  </span>
                  {item.count !== undefined && item.count > 0 && (
                    <span
                      className="text-[11px] font-bold rounded-full px-1.5 leading-tight"
                      style={{
                        backgroundColor: isActive
                          ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 14%, transparent)'
                          : 'var(--theme-bg-muted)',
                        color: isActive ? 'var(--agent-accent, #16a34a)' : 'var(--theme-text-muted)',
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
        <div
          className="flex flex-col flex-1"
          style={{
            background: 'var(--theme-bg-panel)',
            backdropFilter: 'blur(32px)',
            WebkitBackdropFilter: 'blur(32px)',
            boxShadow: '-8px 0 48px rgba(15,23,42,0.12)',
          }}
        >
          {/* Header */}
          <div
            className="flex items-center justify-between px-6 py-4 border-b"
            style={{ borderColor: 'var(--theme-border)' }}
          >
            <div className="flex items-center gap-3">
              <div
                className="flex items-center justify-center rounded-xl"
                style={{
                  width: 36,
                  height: 36,
                  background:
                    'linear-gradient(135deg, var(--agent-accent, #16a34a) 0%, #22c55e 50%, #4ade80 100%)',
                }}
              >
                <Settings size={18} className="text-white" />
              </div>
              <div>
                <div
                  id="settings-panel-title"
                  className="text-[14px] font-bold leading-tight"
                  style={{ color: 'var(--theme-text-primary)' }}
                >
                  Agent 控制中心
                </div>
                <div className="text-[12px] leading-tight" style={{ color: 'var(--theme-text-muted)' }}>
                  Agent Command Center
                </div>
              </div>
              <span
                className="ml-2 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12px] font-medium"
                style={{
                  backgroundColor: 'color-mix(in srgb, var(--agent-accent, #16a34a) 10%, transparent)',
                  color: 'var(--agent-accent, #16a34a)',
                }}
              >
                <span
                  className="inline-block rounded-full"
                  style={{
                    width: 6,
                    height: 6,
                    backgroundColor: 'var(--agent-accent, #16a34a)',
                  }}
                />
                系统在线
              </span>
            </div>

            <button
              onClick={() => setSettingsOpen(false)}
              aria-label="关闭设置"
              className="flex items-center justify-center rounded-lg w-8 h-8 transition-colors hover:bg-[var(--theme-bg-hover)]"
              style={{ color: 'var(--theme-text-muted)' }}
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
