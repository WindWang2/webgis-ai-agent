'use client';

import { useState, useEffect, useRef } from 'react';
import { MessageCircle, Layers, Hash } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { ChatTab } from '@/components/sidebar/chat-tab';
import { LayersTab } from '@/components/sidebar/layers-tab';
import { AssetsTab } from '@/components/sidebar/assets-tab';
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
}

export function LeftSidebar({ open, messages, aiStatus, onSend, accentColor = '#16a34a' }: LeftSidebarProps) {
  const [activeTab, setActiveTab] = useState<LeftTab>('chat');
  const layers = useHudStore((s) => s.layers);
  const analysisAssets = useHudStore((s) => s.analysisAssets);
  const sidebarRef = useRef<HTMLDivElement>(null);

  const tabs: { key: LeftTab; icon: typeof MessageCircle; label: string; badge?: number }[] = [
    { key: 'chat', icon: MessageCircle, label: '对话' },
    { key: 'layers', icon: Layers, label: '图层', badge: layers.length },
    { key: 'assets', icon: Hash, label: '资产', badge: analysisAssets.length },
  ];

  useEffect(() => {
    const el = sidebarRef.current;
    if (!el) return;
    el.style.transform = open ? 'translateX(0)' : 'translateX(-100%)';
  }, [open]);

  return (
    <aside
      ref={sidebarRef}
      className="fixed top-0 left-0 bottom-0 z-40 flex flex-col"
      style={{
        width: 330,
        background: 'rgba(252,253,254,0.90)',
        backdropFilter: 'blur(28px)',
        WebkitBackdropFilter: 'blur(28px)',
        borderRight: '1px solid rgba(255,255,255,0.85)',
        boxShadow: '2px 0 24px rgba(15,23,42,0.09)',
        transform: open ? 'translateX(0)' : 'translateX(-100%)',
        transition: 'transform 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      {/* Tab bar */}
      <div className="flex shrink-0 border-b border-slate-200/60 bg-white/40">
        {tabs.map(({ key, icon: Icon, label, badge }) => {
          const isActive = activeTab === key;
          return (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className="flex-1 flex items-center justify-center gap-1.5 py-3 text-[11.5px] font-medium transition-colors relative"
              style={{
                color: isActive ? accentColor : '#64748b',
              }}
            >
              <Icon size={15} strokeWidth={isActive ? 2.2 : 1.6} />
              <span>{label}</span>
              {badge !== undefined && badge > 0 && (
                <span
                  className="inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full text-[9.5px] font-semibold text-white"
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
          <ChatTab messages={messages} aiStatus={aiStatus} onSend={onSend} accentColor={accentColor} />
        )}
        {activeTab === 'layers' && <LayersTab />}
        {activeTab === 'assets' && <AssetsTab />}
      </div>
    </aside>
  );
}

export default LeftSidebar;
