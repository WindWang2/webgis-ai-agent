'use client';

import { memo, useCallback, useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { useHudStore } from '@/lib/store/useHudStore';
import { useGeolocation } from '@/lib/hooks/use-geolocation';
import { useMapAction } from '@/lib/contexts/map-action-context';

// Refactored custom hooks
import { useWorkspaceSession } from '@/lib/hooks/use-workspace-session';
import { useSSEStream } from '@/lib/hooks/use-sse-stream';

// #553: 会话删除客户端 + 新会话确认守卫
import { deleteSession } from '@/lib/api/chat';
import { describeApiError } from '@/lib/api/transport';
import { hasWorkspaceContent } from '@/lib/utils/workspace-content';
import { ConfirmDialog } from '@/components/shared/confirm-dialog';

// New layout components
import TopBar from '@/components/layout/top-bar';
import { NavRail } from '@/components/layout/nav-rail';
import { ContextPanel } from '@/components/layout/context-panel';
import FloatingLegend from '@/components/map/floating-legend';
import { MapStatusReadout } from '@/components/map/map-status-readout';
import { SpatialCrosshair } from '@/components/map/spatial-crosshair';
import { MapErrorBoundary } from '@/components/map/map-error-boundary';
import { EmbodiedHud } from '@/components/hud/embodied-hud';
import TweaksPanel from '@/components/tweaks-panel';
import { useToastStore } from '@/components/ui/toast';

const RagIndependentPanel = dynamic(() => import('@/components/panel/rag-independent-panel'), { ssr: false });
const HistoryDrawer = dynamic(() => import('@/components/drawers/history-drawer').then(m => ({ default: m.HistoryDrawer })), { ssr: false });
const SettingsPanel = dynamic(() => import('@/components/settings/settings-panel').then(m => ({ default: m.SettingsPanel })), { ssr: false });
const ExportMask = dynamic(() => import('@/components/map/export-mask').then(m => ({ default: m.ExportMask })), { ssr: false });
const TemplateGalleryV2 = dynamic(() => import('@/components/drawers/template-gallery-v2').then(m => ({ default: m.TemplateGalleryV2 })), { ssr: false });

const MapPanel = dynamic(
  () => import('@/components/map/map-panel').then((m) => ({ default: m.MapPanel })),
  {
    ssr: false,
    loading: () => (
      <div className='flex-1 flex items-center justify-center bg-surface-canvas'>
        <div className='animate-pulse text-ink-muted text-micro font-mono uppercase tracking-wider'>
          Loading Map...
        </div>
      </div>
    ),
  }
);

// D-F8: `messages` 是页面级状态，每个 SSE token 批次都会重渲染 Home。
// 这些兄弟面板的 props 在流式期间稳定（layers / handlers / store 内部订阅
// 不受 memo 影响，状态变更仍会触发重渲染），memo 让它们跳过逐批重渲染。
const MemoTopBar = memo(TopBar);
const MemoNavRail = memo(NavRail);
const MemoMapPanel = memo(MapPanel);
const MemoEmbodiedHud = memo(EmbodiedHud);
const MemoSpatialCrosshair = memo(SpatialCrosshair);
const MemoFloatingLegend = memo(FloatingLegend);
const MemoMapStatusReadout = memo(MapStatusReadout);

export default function Home() {
  const { getMapSnapshot, dispatchAction } = useMapAction();
  // FE-07：用单字段 selector 订阅，避免订阅整个 store 导致每次状态变更
  // （视口平移、opsLog push、图层变更等）都触发本组件及全部子树重渲染。
  const layers = useHudStore((s) => s.layers);
  const removeLayer = useHudStore((s) => s.removeLayer);
  const toggleLayer = useHudStore((s) => s.toggleLayer);
  const leftPanelOpen = useHudStore((s) => s.leftPanelOpen);
  const settingsOpen = useHudStore((s) => s.settingsOpen);
  const historyOpen = useHudStore((s) => s.historyOpen);
  const setHistoryOpen = useHudStore((s) => s.setHistoryOpen);
  const hudOpen = useHudStore((s) => s.hudOpen);
  const ragPanelOpen = useHudStore((s) => s.ragPanelOpen);
  const setRagPanelOpen = useHudStore((s) => s.setRagPanelOpen);
  const templatesOpen = useHudStore((s) => s.templatesOpen);
  const setTemplatesOpen = useHudStore((s) => s.setTemplatesOpen);
  const sidebarWidth = useHudStore((s) => s.sidebarWidth);

  const { location: userLocation } = useGeolocation();

  // 1. Session and REST Layers Loader Hook
  const {
    sessionId,
    setSessionId,
    sessionIdRef,
    sessionTokenRef,
    activeSessionToken,
    rememberSessionToken,
    getSessionTokenFor,
    sessions,
    selectSession,
    startNewSession,
    refreshSessions,
  } = useWorkspaceSession(dispatchAction);

  // 3. SSE Stream and Event Bridge Hook
  const {
    messages,
    setMessages,
    aiStatus,
    handleSend,
    handlePlanAction,
    bridge,
  } = useSSEStream(
    sessionId,
    setSessionId,
    sessionIdRef,
    dispatchAction,
    getMapSnapshot,
    userLocation,
    sessionTokenRef,
    rememberSessionToken,
    getSessionTokenFor
  );

  const handleSelectSession = useCallback(
    (sid: string) => {
      // #392: 失败/空会话时 hook 以 (messages, notice) 回传 —— notice 非空
      // 说明恢复失败，渲染成单条错误提示（旧 transcript 已被重置，不再残留）。
      selectSession(sid, (restored, notice) => {
        setMessages(
          notice
            ? [
                {
                  id: `session-error-${Date.now()}`,
                  role: 'assistant' as const,
                  content: notice,
                  timestamp: new Date(),
                },
              ]
            : restored
        );
      });
      setHistoryOpen(false);
    },
    [selectSession, setMessages, setHistoryOpen]
  );

  // #553: 新会话确认守卫读取最新 messages（ref 镜像，避免让 handleNewSession
  // 的引用随每个流式 token 批次变化 —— MemoTopBar 的 memo 依赖 props 稳定）。
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const [confirmNewSession, setConfirmNewSession] = useState(false);

  // 稳定引用：内联箭头会让 RagIndependentPanel 的 Escape 监听在 Home 每次
  // 重渲染（即每个流式 token 批次）时反复解绑/重绑。
  const handleCloseRagPanel = useCallback(() => setRagPanelOpen(false), [setRagPanelOpen]);
  const handleCloseHistory = useCallback(() => setHistoryOpen(false), [setHistoryOpen]);
  const handleCloseTemplates = useCallback(() => setTemplatesOpen(false), [setTemplatesOpen]);

  const startFreshSession = useCallback(() => {
    startNewSession(() => {
      setMessages([
        {
          id: '1',
          role: 'assistant',
          content: '你好！我是 GeoAgent。\n\n我感知地图、分析空间、生成洞察——地图上的一切都是我的一部分。',
          timestamp: new Date(),
        },
      ]);
    });
    setHistoryOpen(false);
    setConfirmNewSession(false);
  }, [startNewSession, setMessages, setHistoryOpen]);

  const handleNewSession = useCallback(() => {
    // #553: 新会话会清空工作区（图层/标注/日志/结果/transcript）。仅当确实
    // 有内容可丢时弹确认；否则（初始欢迎气泡或全空工作区）直接开始。
    const store = useHudStore.getState();
    if (hasWorkspaceContent(messagesRef.current, store.layers, store.annotations, store.opsLog, store.results)) {
      setConfirmNewSession(true);
      return;
    }
    startFreshSession();
  }, [startFreshSession]);

  const handleDeleteSession = useCallback(
    async (sid: string) => {
      try {
        // SEC-08：匿名会话必须带 ownerToken（X-Session-Token），否则后端 404。
        await deleteSession(sid, getSessionTokenFor(sid));
        await refreshSessions();
        if (sessionId === sid) {
          // 删除的是当前会话：重置为新会话，避免 UI 指向服务端已删的会话。
          startFreshSession();
        }
      } catch (err) {
        useToastStore.getState().addToast(
          `删除会话失败：${describeApiError(err, '删除会话失败')}`,
          'error'
        );
      }
    },
    [sessionId, getSessionTokenFor, refreshSessions, startFreshSession]
  );

  // Theme + accent drive CSS custom properties (see the effects below); the
  // shell itself styles from tokens rather than JS colour objects.
  const theme = useHudStore((s) => s.theme);
  const reactiveAccentColor = useHudStore((s) => s.accentColor);
  const fontSize = useHudStore((s) => s.fontSize);

  useEffect(() => {
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
      document.documentElement.setAttribute('data-theme', 'dark');
    } else {
      document.documentElement.classList.remove('dark');
      document.documentElement.setAttribute('data-theme', 'light');
    }
  }, [theme]);

  // UI V4：把 store 的 accentColor 推给 --agent-accent-raw。
  // 之前该变量只有 globals.css 里的静态默认值，nav-rail / context-panel 等
  // 通过 var() 取色的位置永远是默认绿，与 JS 内联取色的组件不一致。
  // 注意写入的是 *-raw：主题校正（暗色下向白色混合）由 globals.css 完成，
  // 组件只需读 var(--agent-accent) 就能拿到当前主题下达标的 accent。
  useEffect(() => {
    document.documentElement.style.setProperty('--agent-accent-raw', reactiveAccentColor);
  }, [reactiveAccentColor]);

  const currentSessionTitle = sessionId
    ? sessions.find((s) => s.id === sessionId)?.title || '新会话'
    : '新会话';

  return (
    <div
      className='h-screen w-screen flex flex-col overflow-hidden bg-surface-canvas'
      style={{ fontSize: `${fontSize}px` }}
    >
      <MemoTopBar
        sessionName={currentSessionTitle}
        onNewSession={handleNewSession}
      />

      <div
        style={{
          flex: 1,
          position: 'relative',
          overflow: 'hidden',
          marginTop: 42,
          marginBottom: 24,
          // UI V3：地图 chrome（图例等）的水平避让偏移 —— nav rail(48) +
          // context panel(可选) 占据的左侧空间。
          ['--workspace-offset' as string]: `${leftPanelOpen ? 48 + sidebarWidth + 12 : 60}px`,
          // UI V4：地图 chrome 的底部基线。workspace 已经预留 24px 状态条，
          // HUD 展开到 210px 时需要再抬 186px。所有底部 chrome（读数条、比例尺、
          // 热力图例、专题图例）都从这一个变量堆叠，因此不会互相压盖 ——
          // 审计发现浮动图例与专题图例此前固定在同一 left/bottom 上必然重叠。
          ['--map-chrome-bottom' as string]: hudOpen ? '196px' : '10px',
        }}
      >
        {/* Map Panel */}
        <div style={{ position: 'absolute', inset: 0 }}>
          <MapErrorBoundary>
            <MemoMapPanel
              layers={layers}
              onRemoveLayer={removeLayer}
              onToggleLayer={toggleLayer}
              onViewportChange={bridge.onViewportChange}
              sessionId={sessionId}
              ownerToken={activeSessionToken}
              sessionTokenRef={sessionTokenRef}
            />
            <ExportMask />
            <MemoSpatialCrosshair />
          </MapErrorBoundary>
        </div>

        {/* Floating heatmap legend — bottom-RIGHT, stacked above the scale bar.
            It used to sit at the same left/bottom as the thematic legend stack,
            where the higher-z thematic card hid it outright. */}
        {layers.find((l) => l.visible && l.type === 'heatmap') && (
          <div
            className='absolute right-3 z-10 transition-[bottom] duration-300'
            style={{ bottom: 'calc(var(--map-chrome-bottom, 10px) + 66px)' }}
          >
            <MemoFloatingLegend />
          </div>
        )}

        {/* Workspace navigation rail + context panel (UI V3) */}
        <MemoNavRail />
        <ContextPanel
          messages={messages}
          aiStatus={aiStatus}
          onSend={handleSend}
          sessionId={sessionId}
          ownerToken={activeSessionToken}
          onPlanAction={handlePlanAction}
        />

        {/* RAG Independent Panel */}
        <RagIndependentPanel open={ragPanelOpen} onClose={handleCloseRagPanel} />

        {/* Map status readout: centre coordinate, zoom, CRS, attribution.
            Anchors the bottom-right chrome column. */}
        <div
          className='absolute right-3 z-10 transition-[bottom] duration-300'
          style={{ bottom: 'var(--map-chrome-bottom, 10px)' }}
        >
          <MemoMapStatusReadout />
        </div>
      </div>

      <MemoEmbodiedHud />

      <HistoryDrawer
        open={historyOpen}
        onClose={handleCloseHistory}
        onSelect={(session) => {
          if (session && session.id) {
            handleSelectSession(session.id);
          } else {
            handleNewSession();
          }
        }}
        onDeleteSession={(session) => {
          void handleDeleteSession(session.id);
        }}
      />

      {/* #553: 新建会话确认 —— 仅当工作区有内容可丢时由 handleNewSession 打开。 */}
      <ConfirmDialog
        open={confirmNewSession}
        title="开始新对话？"
        description="开始新对话将清空当前工作区（地图图层、对话记录）。历史会话仍可在右上角历史记录中找回。"
        confirmLabel="开始新对话"
        onConfirm={startFreshSession}
        onCancel={() => setConfirmNewSession(false)}
      />

      {settingsOpen && <SettingsPanel />}

      {/* Template Gallery V2 — nav rail「模板」入口（与 history/settings 互斥） */}
      <TemplateGalleryV2
        open={templatesOpen}
        onClose={handleCloseTemplates}
        onApply={(t) => useToastStore.getState().addToast(`模板已应用：${t.name}`, 'success')}
      />

      {/* Tweaks Panel Wrapper */}
      <TweaksPanel />
    </div>
  );
}
