'use client';

import { useState, useCallback, useEffect } from 'react';
import dynamic from 'next/dynamic';
import { useHudStore } from '@/lib/store/useHudStore';
import { getThemeColors } from '@/lib/theme';
import { useGeolocation } from '@/lib/hooks/use-geolocation';
import { useMapAction } from '@/lib/contexts/map-action-context';

// Refactored custom hooks
import { useWorkspaceSession } from '@/lib/hooks/use-workspace-session';
import { useMapControl } from '@/lib/hooks/use-map-control';
import { useSSEStream } from '@/lib/hooks/use-sse-stream';

// New layout components
import TopBar from '@/components/layout/top-bar';
import { LeftSidebar } from '@/components/sidebar/left-sidebar';
import FloatingLegend from '@/components/map/floating-legend';
import { SpatialCrosshair } from '@/components/map/spatial-crosshair';
import { EmbodiedHud } from '@/components/hud/embodied-hud';
import RagIndependentPanel from '@/components/panel/rag-independent-panel';
import TweaksPanel from '@/components/tweaks-panel';
import { HistoryDrawer } from '@/components/drawers/history-drawer';
import { SettingsPanel } from '@/components/settings/settings-panel';
import { ExportMask } from '@/components/map/export-mask';

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

export default function Home() {
  const { getMapSnapshot, dispatchAction } = useMapAction();
  const {
    layers,
    removeLayer,
    toggleLayer,
    leftPanelOpen,
    settingsOpen,
    historyOpen,
    setHistoryOpen,
    hudOpen,
    setHudOpen,
    ragPanelOpen,
    setRagPanelOpen,
    sidebarWidth,
  } = useHudStore();

  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const { location: userLocation } = useGeolocation();

  // 1. Session and REST Layers Loader Hook
  const {
    sessionId,
    setSessionId,
    sessionIdRef,
    sessions,
    selectSession,
    startNewSession,
  } = useWorkspaceSession(dispatchAction);

  // 2. Map Control Handlers Hook (zoom, locate, export)
  const {
    handleZoomIn,
    handleZoomOut,
    handleHome,
    handleLocate,
  } = useMapControl(mounted);

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
    userLocation
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
      <TopBar
        sessionName={currentSessionTitle}
        onNewSession={handleNewSession}
      />

      <div style={{ flex: 1, position: 'relative', overflow: 'hidden', marginTop: 42, marginBottom: 24 }}>
        {/* Map Panel */}
        <div style={{ position: 'absolute', inset: 0 }}>
          <MapPanel
            layers={layers}
            onRemoveLayer={removeLayer}
            onToggleLayer={toggleLayer}
            onViewportChange={bridge.onViewportChange}
          />
          <ExportMask />
          <SpatialCrosshair />
        </div>

        {/* Floating Legend */}
        {layers.find((l) => l.visible && l.type === 'heatmap') && (
          <div
            style={{
              position: 'absolute',
              bottom: hudOpen ? 220 : 34,
              left: leftPanelOpen ? sidebarWidth + 14 : 10,
              transition: 'left 0.22s cubic-bezier(0.4,0,0.2,1), bottom 0.3s cubic-bezier(0.4,0,0.2,1)',
              zIndex: 10,
            }}
          >
            <FloatingLegend />
          </div>
        )}

        {/* Left Sidebar */}
        <LeftSidebar
          open={leftPanelOpen}
          messages={messages}
          aiStatus={aiStatus}
          onSend={handleSend}
          accentColor={reactiveAccentColor}
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

      <EmbodiedHud />

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

      {/* Tweaks Panel Wrapper */}
      <TweaksPanel />
    </div>
  );
}
