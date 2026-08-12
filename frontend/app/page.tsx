'use client';

import { memo, useCallback, useEffect } from 'react';
import dynamic from 'next/dynamic';
import { useHudStore } from '@/lib/store/useHudStore';
import { getThemeColors } from '@/lib/theme';
import { useGeolocation } from '@/lib/hooks/use-geolocation';
import { useMapAction } from '@/lib/contexts/map-action-context';

// Refactored custom hooks
import { useWorkspaceSession } from '@/lib/hooks/use-workspace-session';
import { useSSEStream } from '@/lib/hooks/use-sse-stream';

// New layout components
import TopBar from '@/components/layout/top-bar';
import { NavRail } from '@/components/layout/nav-rail';
import { ContextPanel } from '@/components/layout/context-panel';
import FloatingLegend from '@/components/map/floating-legend';
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
      <div className='flex-1 flex items-center justify-center bg-[#dce8f2]'>
        <div className='animate-pulse text-slate-300 text-xs font-mono uppercase tracking-wider'>
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
    sessions,
    selectSession,
    startNewSession,
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
    sessionTokenRef
  );

  const handleSelectSession = useCallback(
    (sid: string) => {
      selectSession(sid, (restored) => setMessages(restored));
      setHistoryOpen(false);
    },
    [selectSession, setMessages, setHistoryOpen]
  );

  const handleNewSession = useCallback(() => {
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
  }, [startNewSession, setMessages, setHistoryOpen]);

  // Read theme colors and dimensions dynamically
  const theme = useHudStore((s) => s.theme);
  const reactiveAccentColor = useHudStore((s) => s.accentColor);
  const fontSize = useHudStore((s) => s.fontSize);
  const colors = getThemeColors(theme);

  useEffect(() => {
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
      document.documentElement.setAttribute('data-theme', 'dark');
    } else {
      document.documentElement.classList.remove('dark');
      document.documentElement.setAttribute('data-theme', 'light');
    }
  }, [theme]);

  const currentSessionTitle = sessionId
    ? sessions.find((s) => s.id === sessionId)?.title || '新会话'
    : '新会话';

  return (
    <div
      style={{
        height: '100vh',
        width: '100vw',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        background: colors.bg,
        fontSize: `${fontSize}px`,
      }}
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
            />
            <ExportMask />
            <MemoSpatialCrosshair />
          </MapErrorBoundary>
        </div>

        {/* Floating Legend */}
        {layers.find((l) => l.visible && l.type === 'heatmap') && (
          <div
            style={{
              position: 'absolute',
              bottom: hudOpen ? 220 : 34,
              left: 'var(--workspace-offset, 60px)',
              transition: 'left 0.22s cubic-bezier(0.4,0,0.2,1), bottom 0.3s cubic-bezier(0.4,0,0.2,1)',
              zIndex: 10,
            }}
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
          accentColor={reactiveAccentColor}
          sessionId={sessionId}
          ownerToken={sessionTokenRef.current}
          onPlanAction={handlePlanAction}
        />

        {/* RAG Independent Panel */}
        <RagIndependentPanel open={ragPanelOpen} onClose={() => setRagPanelOpen(false)} />

        {/* Map attribution */}
        <div
          style={{
            position: 'absolute',
            bottom: 30,
            right: 12,
            fontSize: '11.5px',
            color: theme === 'dark' ? 'rgba(148,163,184,0.6)' : 'rgba(15,23,42,0.35)',
            fontFamily: "'JetBrains Mono', monospace",
            background: theme === 'dark' ? 'rgba(30,41,59,0.72)' : 'rgba(255,255,255,0.72)',
            padding: '2px 8px',
            borderRadius: 4,
            backdropFilter: 'blur(8px)',
            WebkitBackdropFilter: 'blur(8px)',
            zIndex: 10,
          }}
        >
          © OpenStreetMap contributors
        </div>
      </div>

      <MemoEmbodiedHud />

      <HistoryDrawer
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onSelect={(session) => {
          if (session && session.id) {
            handleSelectSession(session.id);
          } else {
            handleNewSession();
          }
        }}
        accentColor={reactiveAccentColor}
      />

      {settingsOpen && <SettingsPanel />}

      {/* Template Gallery V2 — nav rail「模板」入口（与 history/settings 互斥） */}
      <TemplateGalleryV2
        open={templatesOpen}
        onClose={() => setTemplatesOpen(false)}
        onApply={(t) => useToastStore.getState().addToast(`模板已应用：${t.name}`, 'success')}
      />

      {/* Tweaks Panel Wrapper */}
      <TweaksPanel />
    </div>
  );
}
