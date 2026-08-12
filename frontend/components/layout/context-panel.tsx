'use client';

/**
 * ContextPanel — 统一上下文面板（UI V3）。
 *
 * NavRail 负责“去哪”，ContextPanel 负责“当前做什么”。
 * - 统一 PanelHeader（title/description/badge/close），各 tab 不再自建发散头部；
 * - 右缘拖拽调整宽度（280–420px，键盘 ArrowLeft/Right ±16，双击复位 320）；
 * - 折叠时整体 translateX 隐藏且不可聚焦（地图优先）；
 * - tab 内容保持切换即卸载（保留 map-studio isExportMode 与 tasks 轮询语义）。
 */
import { useCallback, useRef, useState } from 'react';
import {
  MessageCircle,
  Folder,
  Database,
  Layers,
  Triangle,
  ListChecks,
  Printer,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import type { AiStatus } from '@/lib/store/hud-types';
import { PanelHeader } from '@/components/shared/panel-header';
import { ChatTab } from '@/components/sidebar/chat-tab';
import { LayersTab } from '@/components/sidebar/layers-tab';
import { AnalysisTab } from '@/components/sidebar/analysis-tab';
import { MapStudioTab } from '@/components/sidebar/map-studio-tab';
import { ProjectTab } from '@/components/sidebar/project-tab';
import { DataSourcesTab } from '@/components/sidebar/data-sources-tab';
import { TasksTab } from '@/components/sidebar/tasks-tab';

export interface ContextPanelProps {
  messages: Array<{
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date | number | null;
    isThinking?: boolean;
    charts?: unknown[];
  }>;
  aiStatus: AiStatus;
  onSend: (text: string) => void;
  accentColor?: string;
  sessionId?: string | null;
  ownerToken?: string | null;
  onPlanAction?: (planId: string, action: 'approve' | 'revise' | 'reject') => void;
}

const PANEL_MIN = 280;
const PANEL_MAX = 420;
const PANEL_DEFAULT = 320;

const clampWidth = (w: number) => Math.min(PANEL_MAX, Math.max(PANEL_MIN, Math.round(w)));

interface PanelMeta {
  icon: LucideIcon;
  title: string;
  description: string;
}

const PANEL_META: Record<string, PanelMeta> = {
  chat: { icon: MessageCircle, title: '对话', description: 'AI 地理智能体' },
  project: { icon: Folder, title: '项目', description: '工作区 · 数据集 · 工作流' },
  data_sources: { icon: Database, title: '数据', description: '空间目录与数据源' },
  layers: { icon: Layers, title: '图层', description: '可见性 · 样式 · 顺序' },
  analysis: { icon: Triangle, title: '分析', description: '空间分析工具' },
  tasks: { icon: ListChecks, title: '任务', description: '后台作业中心' },
  export_layout: { icon: Printer, title: '制图工坊', description: '排版与导出' },
};

export function ContextPanel({
  messages,
  aiStatus,
  onSend,
  accentColor = '#16a34a',
  sessionId,
  ownerToken,
  onPlanAction,
}: ContextPanelProps) {
  const activeTab = useHudStore((s) => s.activeLeftTab);
  const leftPanelOpen = useHudStore((s) => s.leftPanelOpen);
  const toggleLeftPanel = useHudStore((s) => s.toggleLeftPanel);
  const sidebarWidth = useHudStore((s) => s.sidebarWidth);
  const setSidebarWidth = useHudStore((s) => s.setSidebarWidth);
  const layerCount = useHudStore((s) => s.layers.length);
  const exportCount = useHudStore((s) => s.exports.length);

  const metaKey = activeTab === 'exports' ? 'export_layout' : activeTab;
  const meta = PANEL_META[metaKey] ?? PANEL_META.chat;
  const badge = metaKey === 'layers' ? layerCount : metaKey === 'export_layout' ? exportCount : undefined;

  /* ─── 右缘拖拽调宽 ─── */
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef<{ x: number; width: number } | null>(null);

  const onHandlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      dragStart.current = { x: e.clientX, width: sidebarWidth };
      setDragging(true);
      const onMove = (ev: PointerEvent) => {
        if (!dragStart.current) return;
        setSidebarWidth(clampWidth(dragStart.current.width + (ev.clientX - dragStart.current.x)));
      };
      const onUp = () => {
        dragStart.current = null;
        setDragging(false);
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    },
    [sidebarWidth, setSidebarWidth]
  );

  const onHandleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        setSidebarWidth(clampWidth(sidebarWidth + (e.key === 'ArrowRight' ? 16 : -16)));
      }
    },
    [sidebarWidth, setSidebarWidth]
  );

  return (
    <aside
      role="tabpanel"
      id="workspace-panel"
      aria-labelledby={`rail-tab-${metaKey}`}
      aria-hidden={!leftPanelOpen}
      className="fixed bottom-[24px] left-12 top-[42px] z-40 flex flex-col"
      style={{
        width: sidebarWidth,
        maxWidth: 'calc(100vw - 48px)',
        background: 'var(--theme-bg-panel)',
        backdropFilter: 'blur(28px)',
        WebkitBackdropFilter: 'blur(28px)',
        borderRight: '1px solid var(--theme-border)',
        boxShadow: '2px 0 24px rgba(15, 23, 42, 0.09)',
        transform: leftPanelOpen ? 'translateX(0)' : 'translateX(-110%)',
        visibility: leftPanelOpen ? 'visible' : 'hidden',
        transition: dragging
          ? 'none'
          : 'transform 0.25s cubic-bezier(0.4, 0, 0.2, 1), visibility 0.25s',
      }}
    >
      <PanelHeader
        icon={meta.icon}
        title={meta.title}
        description={meta.description}
        badge={badge}
        onClose={toggleLeftPanel}
        id={`workspace-panel-title`}
      />

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {activeTab === 'chat' && (
          <ChatTab
            messages={messages}
            aiStatus={aiStatus}
            onSend={onSend}
            accentColor={accentColor}
            onPlanAction={onPlanAction}
          />
        )}
        {activeTab === 'project' && <ProjectTab />}
        {activeTab === 'layers' && <LayersTab />}
        {activeTab === 'analysis' && <AnalysisTab onSend={onSend} />}
        {activeTab === 'data_sources' && <DataSourcesTab />}
        {(activeTab === 'export_layout' || activeTab === 'exports') && <MapStudioTab />}
        {activeTab === 'tasks' && (
          <TasksTab sessionId={sessionId} ownerToken={ownerToken} accentColor={accentColor} />
        )}
      </div>

      {/* 右缘调宽手柄 */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="调整面板宽度"
        aria-valuenow={sidebarWidth}
        aria-valuemin={PANEL_MIN}
        aria-valuemax={PANEL_MAX}
        title="拖拽调整面板宽度（双击复位）"
        tabIndex={0}
        onPointerDown={onHandlePointerDown}
        onKeyDown={onHandleKeyDown}
        onDoubleClick={() => setSidebarWidth(PANEL_DEFAULT)}
        className="absolute -right-[3px] bottom-0 top-0 w-1.5 cursor-col-resize hover:bg-[var(--agent-accent,#16a34a)]/30"
        style={dragging ? { background: 'var(--agent-accent, #16a34a)', opacity: 0.4 } : undefined}
      />
    </aside>
  );
}

export default ContextPanel;
