'use client';

import { memo, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import dynamic from 'next/dynamic';
import { useHudStore } from '@/lib/store/useHudStore';
import { useGeolocation } from '@/lib/hooks/use-geolocation';
import { useMapAction } from '@/lib/contexts/map-action-context';

// Refactored custom hooks
import { useWorkspaceSession } from '@/lib/hooks/use-workspace-session';
import { useWorkbenchUndoKeys } from '@/lib/workbench/use-undo';
import { useSessionPlan } from '@/lib/hooks/use-session-plan';
import { StreamingChatHost } from '@/components/chat/streaming-chat-host';
import { useChatStore } from '@/lib/store/useChatStore';

// #553: 会话删除客户端 + 新会话确认守卫
import { deleteSession } from '@/lib/api/chat';
import { describeApiError } from '@/lib/api/transport';
import { hasWorkspaceContent } from '@/lib/utils/workspace-content';
import { mapInsetLeft, mapChromeLeft } from '@/lib/utils/workspace-inset';
import { ConfirmDialog } from '@/components/shared/confirm-dialog';
import { setLayerDataSession } from '@/lib/store/layer-data';
import { useRegisterCommands } from '@/lib/commands/registry';
import { CommandPaletteRoot } from '@/components/command/command-palette-root';
import { useQueryConsoleStore } from '@/lib/hooks/use-query-console';
import { useSearchDrawerStore } from '@/lib/hooks/use-search-drawer';
import { useUndoHistoryStore } from '@/lib/hooks/use-undo-history';
import { UndoFlash, UndoHistoryPanel } from '@/components/workbench/undo-history-panel';
import { OnboardingRoot } from '@/components/onboarding/onboarding-root';

// New layout components
import TopBar from '@/components/layout/top-bar';
import { NavRail } from '@/components/layout/nav-rail';
import FloatingLegend from '@/components/map/floating-legend';
import { getCommittedMapSpec, getMapSpecLiveGeneration, subscribeMapSpecLive } from '@/lib/mapspec/session-cursor';
import { MapStatusReadout } from '@/components/map/map-status-readout';
import { SpatialCrosshair } from '@/components/map/spatial-crosshair';
import { MapErrorBoundary } from '@/components/map/map-error-boundary';
import { EmbodiedHud } from '@/components/hud/embodied-hud';
import TweaksPanel from '@/components/tweaks-panel';
import { useToastStore } from '@/components/ui/toast';

const RagIndependentPanel = dynamic(() => import('@/components/panel/rag-independent-panel'), { ssr: false });
const PanelDockHost = dynamic(() => import('@/components/layout/panel-dock').then((m) => m.PanelDockHost), { ssr: false });
const HistoryDrawer = dynamic(() => import('@/components/drawers/history-drawer').then(m => ({ default: m.HistoryDrawer })), { ssr: false });
const SettingsPanel = dynamic(() => import('@/components/settings/settings-panel').then(m => ({ default: m.SettingsPanel })), { ssr: false });
const ExportMask = dynamic(() => import('@/components/map/export-mask').then(m => ({ default: m.ExportMask })), { ssr: false });
const TemplateGalleryV2 = dynamic(() => import('@/components/drawers/template-gallery-v2').then(m => ({ default: m.TemplateGalleryV2 })), { ssr: false });
const QueryConsole = dynamic(() => import('@/components/console/query-console').then(m => ({ default: m.QueryConsole })), { ssr: false });
const SearchDrawer = dynamic(() => import('@/components/search/search-drawer').then(m => ({ default: m.SearchDrawer })), { ssr: false });

const MapPanel = dynamic(
  () => import('@/components/map/map-panel').then((m) => ({ default: m.MapPanel })),
  {
    ssr: false,
    loading: () => (
      <div className='flex-1 flex items-center justify-center bg-surface-canvas'>
        <div className='animate-pulse text-ink-muted text-micro font-mono uppercase tracking-wider'>
          地图加载中…
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
  // GIS Harness 组件面：spec 带启用色条组件时 FloatingLegend 让位
  const mapSpecLiveGen = useSyncExternalStore(
    subscribeMapSpecLive,
    getMapSpecLiveGeneration,
    getMapSpecLiveGeneration,
  );
  const specHasColorbar = useMemo(() => {
    const comps = getCommittedMapSpec()?.layout?.components ?? [];
    return comps.some((c) => c.type === 'continuous_colorbar' && c.enabled !== false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapSpecLiveGen]);
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
    autoRestoreFromAnchor,
  } = useWorkspaceSession(dispatchAction);

  // FRONT-05: messages and active streaming token state decoupled from root Home component.
  // StreamingChatHost owns useSSEStream, isolating 60fps streaming re-renders to the chat subtree.
  const setMessagesRef = useRef<((updater: any) => void) | null>(null);
  const registerSetMessages = useCallback((fn: (updater: any) => void) => {
    setMessagesRef.current = fn;
  }, []);
  const messagesRef = useRef<any[]>([]);
  const handleMessagesChange = useCallback((msgs: any[]) => {
    messagesRef.current = msgs;
  }, []);
  const setMessages = useCallback((updater: any) => {
    setMessagesRef.current?.(updater);
    useChatStore.getState().setMessages(updater);
  }, []);
  const onViewportChangeRef = useRef<((center: [number, number], zoom: number, bearing: number, pitch: number) => void) | null>(null);
  const handleRegisterViewportChange = useCallback(
    (fn: (center: [number, number], zoom: number, bearing: number, pitch: number) => void) => {
      onViewportChangeRef.current = fn;
    },
    []
  );
  const handleViewportChange = useCallback(
    (center: [number, number], zoom: number, bearing: number, pitch: number) => {
      onViewportChangeRef.current?.(center, zoom, bearing, pitch);
    },
    []
  );

  // Workbench V5（W4）：全局 undo/redo 快捷键（Ctrl/⌘+Z、⇑+Z、Ctrl+Y）。
  useWorkbenchUndoKeys();

  // Workbench V5（W11）：刷新自动恢复 —— 锚指向的认证会话走完整恢复管线
  // （仅 mount 一次判定；失败/匿名会话保持新会话语义，不打断用户）。
  const autoRestoreFromAnchorRef = useRef(false);
  useEffect(() => {
    if (autoRestoreFromAnchorRef.current) return;
    autoRestoreFromAnchorRef.current = true;
    autoRestoreFromAnchor((restored, notice) => {
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // #1048: SessionPlan hydrate-then-delta 状态（page 级，与 agentRuntime 同款
  // 下行路径）。增量由 useSSEStream 的分发链驱动，视图经 ContextPanel → ChatTab
  // 到 SessionPlanPanel；applySessionPlanEvent 恒稳，不影响 onEvent 身份。
  const sessionPlan = useSessionPlan(sessionId, activeSessionToken);

  // #667: keep the single lazy-hydration seam's session context in sync
  useEffect(() => {
    setLayerDataSession(sessionId ?? undefined, activeSessionToken ?? null);
  }, [sessionId, activeSessionToken]);

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

  // #553: 新会话确认守卫读取最新 messagesRef（避免让 handleNewSession
  // 的引用随每个流式 token 批次变化 —— MemoTopBar 的 memo 依赖 props 稳定）。
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

  // ADR-0147：会话级命令贡献 —— 依赖本组件持有的会话句柄，按框架约定
  // 动态注册（新会话走 #553 确认守卫；故事视图新开 tab 不打断当前工作区）。
  useRegisterCommands(
    [
      {
        id: 'panel.search',
        title: '跨会话搜索',
        group: '面板',
        keywords: 'search cross-session suosou fulltext',
        run: () => useSearchDrawerStore.getState().openDrawer(),
      },
      {
        id: 'edit.history',
        title: '打开操作历史（撤销/重做时间线）',
        group: '编辑',
        keywords: 'undo history opslog caozuo lishi',
        run: () => useUndoHistoryStore.getState().openPanel(),
      },
      {
        id: 'tools.queryConsole',
        title: '打开高级查询控制台',
        group: '工具',
        keywords: 'query sql console filter chaxun',
        run: () => useQueryConsoleStore.getState().openWith(),
      },
      {
        id: 'session.new',
        title: '新建会话',
        group: '会话',
        keywords: 'new session xinhua hua',
        run: () => handleNewSession(),
      },
      {
        id: 'session.story',
        title: '在故事视图打开当前会话',
        group: '会话',
        keywords: 'story gushi narrative playback',
        when: () => Boolean(sessionIdRef.current),
        run: () => {
          const sid = sessionIdRef.current;
          if (sid) window.open(`/story?session_id=${encodeURIComponent(sid)}`, '_blank');
        },
      },
    ],
    [handleNewSession],
  );

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
    if (/^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$|^rgba?\([0-9,.\s]+\)$/i.test(reactiveAccentColor)) {
      document.documentElement.style.setProperty('--agent-accent-raw', reactiveAccentColor);
    }
  }, [reactiveAccentColor]);

  const currentSessionTitle = sessionId
    ? sessions.find((s) => s.id === sessionId)?.title || '新会话'
    : '新会话';

  return (
    <div
      className='h-screen w-screen flex flex-col overflow-hidden bg-surface-canvas'
      style={{ fontSize: `${fontSize}px` }}
    >
      {/* Wave 11（audit 07 P1）：skip link —— 键盘用户 Tab 首停即「跳到地图」，
          不必穿越 rail/panel 的数十个停靠点。可见性：聚焦即现（sr-only 常规态）。 */}
      <a
        href='#map-canvas'
        className='sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[200] focus:rounded-sm focus:bg-status-accent focus:px-2 focus:py-1 focus:text-caption focus:text-white'
      >
        跳到地图画布
      </a>
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
          // UI V4：地图 chrome（图例等）的水平避让偏移。地图容器真实收缩后
          // （见下方 wrapper），展开时 chrome 只需呼吸间距；收起时避开 rail。
          ['--map-chrome-left' as string]: `${mapChromeLeft(leftPanelOpen)}px`,
          // UI V4：地图 chrome 的底部基线。workspace 已经预留 24px 状态条，
          // HUD 展开到 210px 时需要再抬 186px。所有底部 chrome（读数条、比例尺、
          // 热力图例、专题图例）都从这一个变量堆叠，因此不会互相压盖 ——
          // 审计发现浮动图例与专题图例此前固定在同一 left/bottom 上必然重叠。
          ['--map-chrome-bottom' as string]: hudOpen ? '196px' : '10px',
        }}
      >
        {/* Map Panel — 左栏展开时容器真实收缩（ContextPanel 不再悬浮遮挡）：
            MapLibre 的 ResizeObserver 自动 resize 并把地理中心保持在收缩后
            画布的中心，视口随显示面积重算；位移动画与面板滑入同步（0.25s）。 */}
        <div
          id='map-canvas'
          role='region'
          aria-label='地图画布'
          tabIndex={-1}
          style={{
            position: 'absolute',
            top: 0,
            bottom: 0,
            right: 0,
            left: mapInsetLeft(leftPanelOpen, sidebarWidth),
            transition: 'left 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
          }}
        >
          <MapErrorBoundary>
            <MemoMapPanel
              layers={layers}
              onRemoveLayer={removeLayer}
              onToggleLayer={toggleLayer}
              onViewportChange={handleViewportChange}
              sessionId={sessionId}
              ownerToken={activeSessionToken}
              sessionTokenRef={sessionTokenRef}
            />
            <ExportMask />
            <MemoSpatialCrosshair />
            {/* Workspace V2：停靠面板宿主（右/下 dock；面板实例从 chrome
                面移入停靠区渲染，dock 状态与语义组件状态分离）。 */}
            <PanelDockHost />
          </MapErrorBoundary>
        </div>

        {/* Floating heatmap legend — bottom-RIGHT, stacked above the scale bar.
            It used to sit at the same left/bottom as the thematic legend stack,
            where the higher-z thematic card hid it outright.
            GIS Harness 组件面：spec 已带 continuous_colorbar 组件时让位
            （MapSpecChrome 渲染同一色带，避免同屏两份）。 */}
        {layers.find((l) => l.visible && l.type === 'heatmap') && !specHasColorbar && (
          <div
            className='absolute right-3 z-10 transition-[bottom] duration-300'
            style={{ bottom: 'calc(var(--map-chrome-bottom, 10px) + 66px)' }}
          >
            <MemoFloatingLegend />
          </div>
        )}

        {/* Workspace navigation rail + context panel (UI V3) */}
        <MemoNavRail />
        <StreamingChatHost
          sessionId={sessionId}
          setSessionId={setSessionId}
          sessionIdRef={sessionIdRef}
          dispatchAction={dispatchAction}
          getMapSnapshot={getMapSnapshot}
          userLocation={userLocation}
          sessionTokenRef={sessionTokenRef}
          rememberSessionToken={rememberSessionToken}
          getSessionTokenFor={getSessionTokenFor}
          activeSessionToken={activeSessionToken}
          sessionPlanView={sessionPlan.view}
          applySessionPlanEvent={sessionPlan.applySessionPlanEvent}
          onRegisterSetMessages={registerSetMessages}
          onRegisterViewportChange={handleRegisterViewportChange}
          onMessagesChange={handleMessagesChange}
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

      {/* ADR-0147：命令面板（Ctrl+K）/ 快捷键总览（?）挂载根 */}
      <CommandPaletteRoot />

      {/* ADR-0147：高级查询控制台（data-fabric query 契约消费面） */}
      <QueryConsole sessionId={sessionId} ownerToken={activeSessionToken} />

      {/* ADR-0147：跨会话搜索（本地索引 + 跳转恢复；handleSelectSession 内含抽屉关闭语义） */}
      <SearchDrawer onSelectSession={handleSelectSession} />

      {/* ADR-0147：操作历史弹层 + 撤销/重做可见反馈 */}
      <UndoHistoryPanel />
      <UndoFlash />

      {/* ADR-0147：首次运行引导 + 上下文提示队列（重看入口在设置 → 系统） */}
      <OnboardingRoot />
    </div>
  );
}
