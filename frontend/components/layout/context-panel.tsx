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
  ClipboardList,
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
import { ResultsTab } from '@/components/sidebar/results-tab';
import { PanelErrorBoundary } from '@/components/layout/panel-error-boundary';

export interface ContextPanelProps {
  messages: Array<{
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date | number | null;
    isThinking?: boolean;
    charts?: unknown[];
    resultId?: string;
  }>;
  aiStatus: AiStatus;
  onSend: (text: string) => void;
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
  results: { icon: ClipboardList, title: '分析结果', description: '结果工作台 · 输入 · 指标 · 输出' },
  export_layout: { icon: Printer, title: '制图工坊', description: '排版与导出' },
};

export function ContextPanel({
  messages,
  aiStatus,
  onSend,
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
  const resultCount = useHudStore((s) => s.results.length);
  // HUD 展开时面板整体上移避让（与 nav rail / floating legend 一致），
  // 否则 composer 与 tab 底部内容被 210px HUD 遮住。
  const hudOpen = useHudStore((s) => s.hudOpen);

  const metaKey = activeTab === 'exports' ? 'export_layout' : activeTab;
  const meta = PANEL_META[metaKey] ?? PANEL_META.chat;
  const badge =
    metaKey === 'layers' ? layerCount
    : metaKey === 'export_layout' ? exportCount
    : metaKey === 'results' ? resultCount
    : undefined;

  /* ─── 右缘拖拽调宽 ─── */
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef<{ x: number; width: number } | null>(null);

  // Review P1 修复：pointer capture + cancel/blur 兜底。
  // 之前用 window pointermove/up 监听，指针在窗口外释放（或 pointercancel）
  // 时 onUp 不触发，会留下永久 dragging 状态 + 泄漏的监听器。
  const endDrag = useCallback(() => {
    dragStart.current = null;
    setDragging(false);
  }, []);

  const onHandlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      dragStart.current = { x: e.clientX, width: sidebarWidth };
      setDragging(true);
      // 指针捕获后 move/up/cancel 全部重定向到本元素，无需 window 监听。
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        /* jsdom / 老浏览器无 pointer capture，退化为仅键盘调宽可用 */
      }
    },
    [sidebarWidth]
  );

  const onHandlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragStart.current) return;
      setSidebarWidth(clampWidth(dragStart.current.width + (e.clientX - dragStart.current.x)));
    },
    [setSidebarWidth]
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

  // Review P2 修复：PanelHeader close 收起面板后，焦点从不可见按钮归还到
  // rail 对应 tab。
  const handleClose = useCallback(() => {
    toggleLeftPanel();
    setTimeout(() => {
      document.getElementById(`rail-tab-${metaKey}`)?.focus();
    }, 0);
  }, [toggleLeftPanel, metaKey]);

  return (
    // V4：壳层度量改用 token（left-rail / top-topbar），背景改为不透明
    // surface-panel 并移除 backdrop-blur —— 面板压在持续重绘的地图画布上，
    // blur 是最贵的那一类滤镜，且半透明会让地图细节透进密集文本。
    <aside
      role="tabpanel"
      id="workspace-panel"
      aria-labelledby={`rail-tab-${metaKey}`}
      aria-hidden={!leftPanelOpen}
      className="fixed left-rail top-topbar z-40 flex flex-col border-r border-edge-subtle bg-surface-panel shadow-overlay"
      style={{
        bottom: hudOpen ? 234 : 24,
        width: sidebarWidth,
        maxWidth: 'calc(100vw - var(--railW))',
        transform: leftPanelOpen ? 'translateX(0)' : 'translateX(-110%)',
        visibility: leftPanelOpen ? 'visible' : 'hidden',
        transition: dragging
          ? 'none'
          : 'transform 0.25s cubic-bezier(0.4, 0, 0.2, 1), visibility 0.25s, bottom 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      <PanelHeader
        icon={meta.icon}
        title={meta.title}
        description={meta.description}
        badge={badge}
        onClose={handleClose}
        id={`workspace-panel-title`}
      />

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {activeTab === 'chat' && (
          <ChatTab
            messages={messages}
            aiStatus={aiStatus}
            onSend={onSend}
            onPlanAction={onPlanAction}
          />
        )}
        {activeTab === 'project' && <ProjectTab />}
        {activeTab === 'layers' && <PanelErrorBoundary label="图层"><LayersTab /></PanelErrorBoundary>}
        {activeTab === 'analysis' && <AnalysisTab onSend={onSend} />}
        {activeTab === 'data_sources' && <DataSourcesTab />}
        {(activeTab === 'export_layout' || activeTab === 'exports') && <MapStudioTab />}
        {activeTab === 'tasks' && (
          <PanelErrorBoundary label="任务">
            <TasksTab sessionId={sessionId} ownerToken={ownerToken} />
          </PanelErrorBoundary>
        )}
        {activeTab === 'results' && (
          <PanelErrorBoundary label="结果">
            <ResultsTab sessionId={sessionId} ownerToken={ownerToken} onSend={onSend} />
          </PanelErrorBoundary>
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
        onPointerMove={onHandlePointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={endDrag}
        onKeyDown={onHandleKeyDown}
        onDoubleClick={() => setSidebarWidth(PANEL_DEFAULT)}
        className="absolute -right-[3px] bottom-0 top-0 w-1.5 cursor-col-resize hover:bg-[var(--agent-accent,#16a34a)]/30"
        style={dragging ? { background: 'var(--agent-accent, #16a34a)', opacity: 0.4 } : undefined}
      />
    </aside>
  );
}

export default ContextPanel;
