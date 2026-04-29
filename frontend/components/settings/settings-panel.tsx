'use client';

import React from 'react';
import {
  Sparkles,
  Server,
  Hash,
  Brain,
  Crosshair,
  Settings,
  X,
  ShieldCheck,
} from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { LlmConfig } from './llm-config';
import { McpServers } from './mcp-servers';
import { SkillsHub } from './skills-hub';
import { RagConfig } from './rag-config';
import { MapConfig } from './map-config';
import { SystemSettings } from './system-settings';

/* ------------------------------------------------------------------ */
/*  Nav item definition                                                */
/* ------------------------------------------------------------------ */

interface NavItem {
  key: 'llm' | 'mcp' | 'skills' | 'rag' | 'map' | 'system';
  label: string;
  icon: React.ElementType;
  count?: number;
}

const NAV_ITEMS: NavItem[] = [
  { key: 'llm', label: '大模型', icon: Sparkles },
  { key: 'mcp', label: 'MCP服务', icon: Server, count: 0 },
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
    case 'mcp':
      return <McpServers />;
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

  const mcpServers = useHudStore((s) => s.mcpServers);
  const skills = useHudStore((s) => s.skills);

  if (!settingsOpen) return null;

  const activeMcpCount = mcpServers.filter((s) => s.status === 'active').length;
  const enabledSkillCount = skills.filter((s) => s.enabled).length;

  const navWithCounts: NavItem[] = NAV_ITEMS.map((item) => {
    if (item.key === 'mcp') return { ...item, count: activeMcpCount };
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
        className="fixed inset-y-0 right-0 z-[101] flex animate-slide-from-right"
        style={{ width: 720 }}
      >
        {/* Left nav rail */}
        <nav
          className="flex flex-col border-r border-slate-900/10 bg-[rgba(250,251,252,0.97)] backdrop-blur-[32px] py-4"
          style={{ width: 136, flexShrink: 0 }}
        >
          {/* Header mini */}
          <div className="px-4 mb-4">
            <div className="flex items-center gap-1.5">
              <ShieldCheck size={14} className="text-green-600" />
              <span className="text-[11px] font-semibold text-slate-700">
                控制中心
              </span>
            </div>
          </div>

          {/* Nav items */}
          <div className="flex flex-col gap-0.5 px-2 flex-1">
            {navWithCounts.map((item) => {
              const Icon = item.icon;
              const isActive = settingsTab === item.key;
              return (
                <button
                  key={item.key}
                  onClick={() => setSettingsTab(item.key)}
                  className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-left transition-all duration-150"
                  style={{
                    backgroundColor: isActive
                      ? 'rgba(22,163,74,0.08)'
                      : 'transparent',
                    color: isActive ? '#16a34a' : '#475569',
                  }}
                >
                  <Icon
                    size={16}
                    style={{
                      color: isActive ? '#16a34a' : '#94a3b8',
                    }}
                  />
                  <span
                    className="text-[12px] font-medium truncate flex-1"
                    style={{ color: isActive ? '#16a34a' : '#475569' }}
                  >
                    {item.label}
                  </span>
                  {item.count !== undefined && item.count > 0 && (
                    <span
                      className="text-[10px] font-bold rounded-full px-1.5 leading-tight"
                      style={{
                        backgroundColor: isActive
                          ? 'rgba(22,163,74,0.12)'
                          : 'rgba(15,23,42,0.06)',
                        color: isActive ? '#16a34a' : '#94a3b8',
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
          className="flex flex-col flex-1 bg-[rgba(250,251,252,0.97)] backdrop-blur-[32px]"
          style={{
            boxShadow: '-8px 0 48px rgba(15,23,42,0.12)',
          }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-slate-900/10">
            <div className="flex items-center gap-3">
              <div
                className="flex items-center justify-center rounded-xl"
                style={{
                  width: 36,
                  height: 36,
                  background:
                    'linear-gradient(135deg, #16a34a 0%, #22c55e 50%, #4ade80 100%)',
                }}
              >
                <Settings size={18} className="text-white" />
              </div>
              <div>
                <div className="text-[15px] font-bold text-slate-800 leading-tight">
                  Agent 控制中心
                </div>
                <div className="text-[11px] text-slate-400 leading-tight">
                  Agent Command Center
                </div>
              </div>
              <span
                className="ml-2 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium"
                style={{
                  backgroundColor: 'rgba(22,163,74,0.08)',
                  color: '#16a34a',
                }}
              >
                <span
                  className="inline-block rounded-full"
                  style={{
                    width: 6,
                    height: 6,
                    backgroundColor: '#16a34a',
                  }}
                />
                系统在线
              </span>
            </div>

            <button
              onClick={() => setSettingsOpen(false)}
              className="flex items-center justify-center rounded-lg w-8 h-8 text-slate-400 hover:text-slate-600 hover:bg-slate-100/80 transition-colors"
            >
              <X size={18} />
            </button>
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto px-6 py-5">
            <TabContent tab={settingsTab} />
          </div>
        </div>
      </div>
    </>
  );
}
