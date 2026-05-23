'use client';

import { MessageCircle, Layers, Printer } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { ChatTab } from '@/components/sidebar/chat-tab';
import { LayersTab } from '@/components/sidebar/layers-tab';
import { MapStudioTab } from '@/components/sidebar/map-studio-tab';
import type { AiStatus, LeftTab } from '@/lib/store/hud-types';

export interface LeftSidebarProps {
  open: boolean;
  messages: Array<{
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date;
    isThinking?: boolean;
    charts?: unknown[];
  }>;
  aiStatus: AiStatus;
  onSend: (text: string) => void;
  accentColor?: string;
  /** Plan Mode: 由 page.tsx 注入 — 用户在 PlanProposalCard 上点按钮时回调 */
  onPlanAction?: (planId: string, action: 'approve' | 'revise' | 'reject') => void;
}

interface TabDef {
  key: LeftTab;
  icon: LucideIcon;
  label: string;
}

const TAB_DEFS: TabDef[] = [
  { key: 'chat', icon: MessageCircle, label: '对话' },
  { key: 'layers', icon: Layers, label: '图层' },
  { key: 'export_layout', icon: Printer, label: '制图工坊' },
];

export function LeftSidebar({ open, messages, aiStatus, onSend, accentColor = '#16a34a', onPlanAction }: LeftSidebarProps) {
  const activeTab = useHudStore((s) => s.activeLeftTab);
  const setActiveTab = useHudStore((s) => s.setActiveLeftTab);
  const sidebarWidth = useHudStore((s) => s.sidebarWidth);
  const layers = useHudStore((s) => s.layers);
  const exports = useHudStore((s) => s.exports);

  const theme = useHudStore((s) => s.theme);
  const isDark = theme === 'dark';

  // Compute badges for visible tabs
  const badges: Record<string, number | undefined> = {
    chat: undefined,
    layers: layers.length > 0 ? layers.length : undefined,
    export_layout: exports.length > 0 ? exports.length : undefined,
  };

  return (
    <aside
      className="fixed top-[42px] left-0 bottom-[24px] z-40 flex flex-col"
      style={{
        width: sidebarWidth,
        maxWidth: '90vw',
        background: isDark ? 'rgba(9, 9, 11, 0.85)' : 'rgba(252,253,254,0.90)',
        backdropFilter: 'blur(28px)',
        WebkitBackdropFilter: 'blur(28px)',
        borderRight: isDark ? '1px solid rgba(255,255,255,0.06)' : '1px solid rgba(255,255,255,0.85)',
        boxShadow: isDark ? '2px 0 24px rgba(0, 0, 0, 0.4)' : '2px 0 24px rgba(15,23,42,0.09)',
        transform: open ? 'translateX(0)' : 'translateX(-100%)',
        transition: 'transform 0.3s cubic-bezier(0.4, 0, 0.2, 1), width 0.2s ease',
      }}
    >
      {/* Tab bar */}
      <div className={`flex shrink-0 border-b ${isDark ? 'border-zinc-800/60 bg-zinc-950/40' : 'border-slate-200/60 bg-white/40'}`}>
        {TAB_DEFS.map(({ key, icon: Icon, label }) => {
          const isActive = activeTab === key || (key === 'export_layout' && (activeTab === 'export_layout' || activeTab === 'exports'));
          const badge = badges[key];
          return (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex-1 flex items-center justify-center gap-1.5 py-3 text-[11.5px] font-medium transition-colors relative ${isDark ? 'hover:bg-zinc-800/20' : 'hover:bg-slate-50/50'}`}
              style={{
                color: isActive ? accentColor : (isDark ? '#94a3b8' : '#64748b'),
              }}
            >
              <Icon size={15} strokeWidth={isActive ? 2.2 : 1.6} />
              <span>{label}</span>
              {badge !== undefined && badge > 0 && (
                <span
                  className="inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full text-[9.5px] font-semibold text-white animate-pulse"
                  style={{ backgroundColor: accentColor }}
                >
                  {badge}
                </span>
              )}
              {isActive && (
                <span
                  className="absolute bottom-0 left-1/2 -translate-x-1/2 h-[2px] rounded-full w-8"
                  style={{ backgroundColor: accentColor }}
                />
              )}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === 'chat' && (
          <ChatTab messages={messages} aiStatus={aiStatus} onSend={onSend} accentColor={accentColor} onPlanAction={onPlanAction} />
        )}
        {activeTab === 'layers' && <LayersTab />}
        {(activeTab === 'export_layout' || activeTab === 'exports') && <MapStudioTab />}
      </div>
    </aside>
  );
}

export default LeftSidebar;
