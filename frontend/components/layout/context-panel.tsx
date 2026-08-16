'use client';

/**
 * ContextPanel — 统一上下文面板（UI V3）。
 *
 * NavRail 负责“去哪”，ContextPanel 负责“当前做什么”。
 * - 统一 PanelHeader（title/description/badge/close），各 tab 不再自建发散头部；
 * - 右缘拖拽调整宽度（280–420px，键盘 ArrowLeft/Right ±16，双击复位 320）；
 * - 折叠时整体 translateX 隐藏且不可聚焦（地图优先）；
 * - tab 内容保持切换即卸载（保留 map-studio isExportMode 与 tasks 轮询语义）。
 *
 * 拖拽调宽的数据通路（perf 收敛）：
 *   pointerdown → 记录起点 + 命令式 CSS 变量 --panel-draft-w（不进 React 状态）
 *   pointermove → 只更新 ref 里的草稿宽度，RAF 逐帧写到 <aside>（无全局 store 写）
 *   终止（pointerup/cancel/lostpointercapture/blur/折叠/卸载）
 *            → 取消 RAF + 摘除监听 + 恰好一次 setSidebarWidth 提交
 * 旧实现在每个原始 pointermove 上写全局 store，导致本面板（含重型活动 tab）
 * 与订阅 sidebarWidth 的 page.tsx 全部逐事件重渲染。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
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
const PANEL_STEP = 16;

const clampWidth = (w: number) => Math.min(PANEL_MAX, Math.max(PANEL_MIN, Math.round(w)));

/** 一次拖拽的全部可变状态：只在 ref 里演进，pointermove 不触碰 React/store。 */
interface DragState {
  pointerId: number;
  startX: number;
  startWidth: number;
  /** 最新（尚未必已 paints）的钳制草稿宽度。 */
  width: number;
  rafId: number | null;
  /** 一帧内已排队未画（含 rAF 被同步执行的测试桩的场景）。 */
  rafPending: boolean;
  /** 拖拽期间挂上的 window 监听（blur + capture 兜底）的摘除函数。 */
  detachListeners: Array<() => void>;
}

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

  /* ─── 右缘拖拽调宽：草稿走命令式 CSS 变量，终止时一次性提交 store ─── */
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<DragState | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  const separatorRef = useRef<HTMLDivElement | null>(null);
  // 关闭面板后待归还焦点的 rail tab（见 handleClose / leftPanelOpen effect）。
  const pendingFocusTab = useRef<string | null>(null);

  // 把草稿宽度写到 <aside> 的 CSS 变量与 separator 的 aria-valuenow。
  // 拖拽期间 render 用 width: var(--panel-draft-w)，因此这些命令式写入
  // 不会被无关重渲染覆盖，也不需要触发任何重渲染。
  const applyDraft = useCallback((w: number) => {
    panelRef.current?.style.setProperty('--panel-draft-w', `${w}px`);
    separatorRef.current?.setAttribute('aria-valuenow', String(w));
  }, []);

  // RAF 合帧：pointermove 事件频率可能高于刷新率，同一帧内多次 style 写入
  // 只保留最后一次；rafPending 与 dragRef 判同保证过期帧回调（终止后、或
  // rAF 被同步执行的测试桩）不会把旧宽度落到 DOM 或卡住后续帧。
  const scheduleApply = useCallback(() => {
    const d = dragRef.current;
    if (!d || d.rafPending) return;
    d.rafPending = true;
    const id = requestAnimationFrame(() => {
      d.rafPending = false;
      if (dragRef.current !== d) return; // 过期帧：拖拽已终止
      applyDraft(d.width);
    });
    d.rafId = id;
  }, [applyDraft]);

  // 唯一的终止函数，所有路径幂等（dragRef 判空即返回）：
  // 取消 RAF → 摘除 window 监听 → 若宽度实际变化恰好提交一次 →
  // dragging=false 让 render 切回 store 宽度。
  const terminateDrag = useCallback(() => {
    const d = dragRef.current;
    if (!d) return;
    dragRef.current = null;
    if (d.rafId !== null) cancelAnimationFrame(d.rafId);
    d.detachListeners.forEach((detach) => detach());
    applyDraft(d.width); // 先让可见宽度 == 即将提交的宽度
    if (d.width !== d.startWidth) setSidebarWidth(d.width);
    setDragging(false);
  }, [applyDraft, setSidebarWidth]);

  // window blur（Alt-Tab / 系统打断）时捕获语义不再可靠 —— 按当前草稿收尾。
  const handleWindowBlur = useCallback(() => terminateDrag(), [terminateDrag]);

  // pointer capture 不可用（老浏览器/jsdom）时的有界兜底：window 级
  // pointermove/up/cancel，保证指针离开 8px 手柄后拖拽仍可终止。
  const onFallbackMove = useCallback(
    (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d || e.pointerId !== d.pointerId) return;
      d.width = clampWidth(d.startWidth + (e.clientX - d.startX));
      scheduleApply();
    },
    [scheduleApply]
  );
  const onFallbackEnd = useCallback(
    (e: PointerEvent) => {
      if (dragRef.current?.pointerId === e.pointerId) terminateDrag();
    },
    [terminateDrag]
  );

  const onHandlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return; // 仅主键/触摸
      // 拖拽已在进行（第二根手指/画笔落在手柄上）：忽略新指针，而不是
      // 覆盖在途 DragState —— 否则前一个指针的草稿被无声丢弃（视觉回跳）。
      if (dragRef.current) return;
      e.preventDefault();
      const d: DragState = {
        pointerId: e.pointerId,
        startX: e.clientX,
        startWidth: sidebarWidth,
        width: sidebarWidth,
        rafId: null,
        rafPending: false,
        detachListeners: [],
      };
      dragRef.current = d;
      applyDraft(d.width); // 先初始化变量，render 切到 var() 时无回跳
      setDragging(true);
      window.addEventListener('blur', handleWindowBlur);
      d.detachListeners.push(() => window.removeEventListener('blur', handleWindowBlur));
      let captured = false;
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
        captured = e.currentTarget.hasPointerCapture(e.pointerId);
      } catch {
        captured = false;
      }
      if (!captured) {
        window.addEventListener('pointermove', onFallbackMove);
        window.addEventListener('pointerup', onFallbackEnd);
        window.addEventListener('pointercancel', onFallbackEnd);
        d.detachListeners.push(() => {
          window.removeEventListener('pointermove', onFallbackMove);
          window.removeEventListener('pointerup', onFallbackEnd);
          window.removeEventListener('pointercancel', onFallbackEnd);
        });
      }
    },
    [sidebarWidth, applyDraft, handleWindowBlur, onFallbackMove, onFallbackEnd]
  );

  // 捕获路径：capture 把 move/up/cancel 全部重定向到本元素。
  const onHandlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const d = dragRef.current;
      if (!d || e.pointerId !== d.pointerId) return;
      d.width = clampWidth(d.startWidth + (e.clientX - d.startX));
      scheduleApply();
    },
    [scheduleApply]
  );

  const endIfPointer = useCallback(
    (pointerId: number) => {
      if (dragRef.current?.pointerId === pointerId) terminateDrag();
    },
    [terminateDrag]
  );

  const onHandleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        const delta = e.key === 'ArrowRight' ? PANEL_STEP : -PANEL_STEP;
        const d = dragRef.current;
        if (d) {
          // 拖拽进行中按方向键（双手操作）：增量并入草稿而不是直写 store，
          // 否则 store 与可见宽度中途分叉、且释放时的提交会覆盖键盘值。
          d.width = clampWidth(d.width + delta);
          scheduleApply();
        } else {
          setSidebarWidth(clampWidth(sidebarWidth + delta));
        }
      }
    },
    [sidebarWidth, setSidebarWidth, scheduleApply]
  );

  // 卸载兜底：取消在途 RAF / 摘监听。不做 store 提交、不 setState
  // （组件已卸载；store 里保留拖拽前宽度即可）。
  useEffect(
    () => () => {
      const d = dragRef.current;
      if (!d) return;
      dragRef.current = null;
      if (d.rafId !== null) cancelAnimationFrame(d.rafId);
      d.detachListeners.forEach((detach) => detach());
    },
    []
  );

  // 拖拽中面板被折叠（rail 再次点击 active tab / close 按钮）：立即收尾，
  // 否则面板已 visibility:hidden 而捕获仍在把事件重定向给手柄。
  useEffect(() => {
    if (!leftPanelOpen) terminateDrag();
  }, [leftPanelOpen, terminateDrag]);

  // Review P2 修复（V2）：close 收起面板后，焦点从不可见的 close 按钮归还到
  // rail 对应 tab。用 leftPanelOpen 提交后的 effect 替代旧 setTimeout(0) 竞态：
  // effect 在面板隐藏落地后运行，focus 不会被浏览器随后踢回 body；
  // 若用户此刻已把焦点移到别处（快速切换 tab），则不打扰。
  const handleClose = useCallback(() => {
    terminateDrag(); // 拖拽中点 close 的防御路径
    pendingFocusTab.current = metaKey;
    toggleLeftPanel();
  }, [terminateDrag, toggleLeftPanel, metaKey]);

  useEffect(() => {
    if (leftPanelOpen || pendingFocusTab.current === null) return;
    const key = pendingFocusTab.current;
    pendingFocusTab.current = null;
    const target = document.getElementById(`rail-tab-${key}`);
    const active = document.activeElement;
    const focusNeedsRestore =
      !active || active === document.body || panelRef.current?.contains(active);
    if (target && focusNeedsRestore) target.focus();
  }, [leftPanelOpen]);

  return (
    // V4：壳层度量改用 token（left-rail / top-topbar），背景改为不透明
    // surface-panel 并移除 backdrop-blur —— 面板压在持续重绘的地图画布上，
    // blur 是最贵的那一类滤镜，且半透明会让地图细节透进密集文本。
    <aside
      ref={panelRef}
      role="tabpanel"
      id="workspace-panel"
      aria-labelledby={`rail-tab-${metaKey}`}
      aria-hidden={!leftPanelOpen}
      className="fixed left-rail top-topbar z-40 flex flex-col border-r border-edge-subtle bg-surface-panel shadow-overlay"
      style={{
        bottom: hudOpen ? 234 : 24,
        // 拖拽中读命令式草稿变量（pointermove 不产生重渲染）；平时读 store。
        width: dragging ? 'var(--panel-draft-w)' : sidebarWidth,
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
        {/* #463: sessionId/ownerToken are threaded into the Data Sources tab so
            实例化至图层 materializes into the REAL conversation session instead of
            the phantom 'default_session' (and the layer's ref is fetchable). */}
        {activeTab === 'data_sources' && <DataSourcesTab sessionId={sessionId} ownerToken={ownerToken} />}
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

      {/* 右缘调宽手柄：8px 命中区跨在面板边框上（-right-1 + w-2），
          可见部分只是 hover/focus/drag 时的 accent 着色，无布局位移。
          注意 hover/focus 着色必须用可编译的 token 类：Tailwind 3 对
          var() 颜色加 /NN 透明度修饰符会静默丢弃整个规则（旧实现的
          hover:bg-[var(--agent-accent,#16a34a)]/30 从未生效过）。 */}
      <div
        ref={separatorRef}
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
        onPointerUp={(e) => endIfPointer(e.pointerId)}
        onPointerCancel={(e) => endIfPointer(e.pointerId)}
        onLostPointerCapture={(e) => endIfPointer(e.pointerId)}
        onKeyDown={onHandleKeyDown}
        onDoubleClick={() => setSidebarWidth(PANEL_DEFAULT)}
        className="absolute -right-1 bottom-0 top-0 w-2 cursor-col-resize touch-none hover:bg-status-accent-soft focus-visible:bg-status-accent-soft"
        style={dragging ? { background: 'var(--agent-accent)', opacity: 0.4 } : undefined}
      />
    </aside>
  );
}

export default ContextPanel;
