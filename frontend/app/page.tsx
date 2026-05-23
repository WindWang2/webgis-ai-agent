'use client';
import { useState, useCallback, useEffect, useRef } from 'react';
import dynamic from 'next/dynamic';
import { useHudStore } from '@/lib/store/useHudStore';
import { getThemeColors } from '@/lib/theme';
import type { SSEEvent } from '@/lib/api/chat';
import { useMapBridge } from '@/lib/hooks/useMapBridge';
import { useWebSocket } from '@/lib/hooks/use-websocket';
import { useGeolocation } from '@/lib/hooks/use-geolocation';
import type { ChatSession } from '@/lib/types/chat';
import type { AgentPlanState } from '@/lib/types/agent-plan';
import { API_BASE } from '@/lib/api/config';
import { useMapAction } from '@/lib/contexts/map-action-context';

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

type ToolCallEntry = {
  id: string;
  tool: string;
  arguments?: string;
  status: 'running' | 'completed' | 'failed';
  result?: any;
  hasGeojson?: boolean;
  error?: string;
  startedAt?: number;
  completedAt?: number;
};

// Plan Mode：propose_plan 工具的结果摘要，挂到对应消息上由 PlanProposalCard 渲染
export type PlanProposalPayload = {
  plan_id: string;
  title: string;
  summary?: string;
  step_count: number;
  destructive_steps?: string[];
  steps_preview?: Array<{ id: string; tool: string; purpose?: string; destructive?: boolean }>;
  status: 'pending' | 'approved' | 'rejected';
};

export default function Home() {
  const { getMapSnapshot, dispatchAction } = useMapAction();
  const {
    layers,
    addLayer,
    removeLayer,
    toggleLayer,
    clearLayers,
    setLayers,
    leftPanelOpen,
    settingsOpen,
    historyOpen,
    setHistoryOpen,
    hudOpen,
    setHudOpen,
    ragPanelOpen,
    setRagPanelOpen,
    tweaksOpen,
    setTweaksOpen,
    setRagResults,
    setExports,
    pushOpLog,
    clearOpsLog,
    clearCausalChain,
    setSessions: setStoreSessions,
  } = useHudStore();

  /* ─── Chat state ─── */
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const [messages, setMessages] = useState<Array<{ id: string; role: 'user' | 'assistant'; content: string; timestamp: any; isThinking?: boolean; charts?: unknown[]; toolCalls?: ToolCallEntry[]; plan?: PlanProposalPayload; agentPlan?: AgentPlanState }>>([
    {
      id: '1',
      role: 'assistant',
      content: '你好！我是 GeoAgent。\n\n我感知地图、分析空间、生成洞察——地图上的一切都是我的一部分。\n\n试着告诉我：\n- 分析北京市学校分布密度\n- 成都市人口热力图\n- 计算各区 POI覆盖率',
      timestamp: null,
    },
  ]);
  const [sessionId, setSessionId] = useState<string>();
  const sessionIdRef = useRef<string | undefined>(undefined);
  const thinkingMsgIdRef = useRef<string>('');
  const rawContentRef = useRef<string>('');

  /* ─── Session history state ─── */
  const [sessions, setSessions] = useState<ChatSession[]>([]);

  // Sync sessions to store for HistoryDrawer
  useEffect(() => {
    setStoreSessions(sessions.map(s => ({
      id: s.id,
      title: s.title || '未命名',
      time: new Date(s.createdAt).toLocaleString('zh-CN') || '',
      msgs: s.messages?.length || 0,
      tags: [],
    })));
  }, [sessions, setStoreSessions]);

  // Fetch session list on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/v1/chat/sessions`)
      .then(res => res.json())
      .then(data => {
        if (data.sessions) setSessions(data.sessions);
      })
      .catch(err => console.error('Fetch sessions failed:', err));
  }, []);

  // F6: 用 ref 持有当前激活的 AbortController；快速连点会话时把上一次取消，
  // 避免上一会话的 _refId fetch 在新会话里把过期图层 addLayer 进去。
  const sessionLoadAbortRef = useRef<AbortController | null>(null);

  const handleSelectSession = useCallback(async (sid: string) => {
    // 取消上一次仍在飞的会话恢复请求
    sessionLoadAbortRef.current?.abort();
    const ctrl = new AbortController();
    sessionLoadAbortRef.current = ctrl;
    const signal = ctrl.signal;

    clearLayers();
    try {
      const res = await fetch(`${API_BASE}/api/v1/chat/sessions/${sid}`, { signal });
      const data = await res.json();
      if (signal.aborted) return;
      if (data.messages && data.messages.length > 0) {
        const restored = data.messages.map((m: any) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          timestamp: new Date(m.timestamp),
        }));
        restored.push({
          id: `session-switch-${Date.now()}`,
          role: 'assistant',
          content: `已恢复历史会话「${data.title || '未命名'}」——共 ${data.messages.length} 条记录。可继续提问。`,
          timestamp: new Date(),
        });
        setMessages(restored);
      }
      setSessionId(sid);
      setHistoryOpen(false);

      const stateRes = await fetch(`${API_BASE}/api/v1/chat/sessions/${sid}/map-state`, { signal });
      if (signal.aborted) return;
      if (stateRes.ok) {
        const stateData = await stateRes.json();
        const state = stateData?.map_state;
        if (state) {
          const store = useHudStore.getState();
          if (state.viewport) {
            dispatchAction({ command: 'fly_to', params: { center: state.viewport.center, zoom: state.viewport.zoom, bearing: state.viewport.bearing, pitch: state.viewport.pitch } });
          }
          if (state.base_layer) store.setBaseLayer(state.base_layer);
          for (const layer of state.layers || []) {
            if (layer._refId && layer._refId.startsWith('ref:')) {
              fetch(`${API_BASE}/api/v1/layers/data/${layer._refId}?session_id=${sid}`, { signal })
                .then(r => r.ok ? r.json() : null)
                .then(geojson => {
                  if (signal.aborted) return;
                  if (geojson && (geojson.type === 'FeatureCollection' || geojson.features)) {
                    store.addLayer({ ...layer, source: geojson });
                  }
                })
                .catch((err) => {
                  // AbortError 是预期的，其它错误打日志
                  if (err?.name !== 'AbortError') console.error('[LayerFetch]', err);
                });
            }
          }
        }
      }
    } catch (err: any) {
      // F6: 取消是正常的、不报错
      if (err?.name === 'AbortError') return;
      console.error('Load session failed:', err);
    }
  }, [setHistoryOpen, clearLayers, dispatchAction]);

  const handleNewSession = useCallback(() => {
    setSessionId(undefined);
    setMessages([{
      id: '1',
      role: 'assistant',
      content: '你好！我是 GeoAgent。\n\n我感知地图、分析空间、生成洞察——地图上的一切都是我的一部分。',
      timestamp: new Date(),
    }]);
    clearLayers();
    clearOpsLog();
    clearCausalChain();
    localStorage.removeItem('webgis_session_id');
    setHistoryOpen(false);
  }, [setHistoryOpen, clearLayers, clearOpsLog, clearCausalChain]);

  useWebSocket(sessionId);
  const { location: userLocation } = useGeolocation();

  // Keep sessionIdRef in sync for use in onEvent (avoids stale closure)
  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);

  /* ─── Map control functions ─── */
  const handleZoomIn = useCallback(() => {
    const { viewport, setViewport } = useHudStore.getState();
    setViewport(viewport.center, Math.min(viewport.zoom + 1, 22), viewport.bearing, viewport.pitch);
  }, []);

  const handleZoomOut = useCallback(() => {
    const { viewport, setViewport } = useHudStore.getState();
    setViewport(viewport.center, Math.max(viewport.zoom - 1, 1), viewport.bearing, viewport.pitch);
  }, []);

  const handleHome = useCallback(() => {
    const { setViewport } = useHudStore.getState();
    setViewport([116.4074, 39.9042], 4.0, 0, 0);
  }, []);

  const handleLocate = useCallback(() => {
    const { setViewport, pushOpLog } = useHudStore.getState();
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          setViewport([pos.coords.longitude, pos.coords.latitude], 12.0, 0, 0);
          pushOpLog({
            id: Date.now().toString(),
            type: 'flyto',
            label: '飞到 — 当前位置',
            time: mounted ? new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '',
            detail: `[${pos.coords.longitude.toFixed(5)}, ${pos.coords.latitude.toFixed(5)}]`,
          });
        },
        () => {
          // Fallback if geolocation fails
          setViewport([116.4074, 39.9042], 10.0, 0, 0);
        }
      );
    }
  }, []);

  const handleExport = useCallback(() => {
    const { setExports, pushOpLog, exports } = useHudStore.getState();
    pushOpLog({
      id: Date.now().toString(),
      type: 'add',
      label: '导出 — 地图快照',
      time: mounted ? new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '',
      detail: '保存为 PNG',
    });
    setExports([
      { id: Date.now().toString(), name: '地图快照', type: 'png', size: '1.2 MB', date: '刚刚' },
      ...exports,
    ]);
  }, []);

  /* ─── SSE event router + bridge ─── */
  const parseThink = useCallback((raw: string) => {
    const start = raw.indexOf('<think>');
    const end = raw.indexOf('</think>');
    if (start !== -1 && end !== -1 && end > start) {
      return { thinking: raw.slice(start + 7, end), content: raw.slice(0, start) + raw.slice(end + 8).trimStart() };
    }
    if (start !== -1) {
      return { thinking: raw.slice(start + 7), content: raw.slice(0, start) };
    }
    return { thinking: '', content: raw };
  }, []);

  const onEvent = useCallback((event: SSEEvent) => {
    const data = event.data as any;

    // Session ID assignment (first response carries the server-assigned session)
    if (data?.session_id && data.session_id !== sessionIdRef.current) {
      setSessionId(data.session_id);
      sessionIdRef.current = data.session_id;
    }

    const thinkingId = thinkingMsgIdRef.current;

    if (event.event === 'token' || event.event === 'content') {
      const chunk = data.content || '';
      if (data.is_reasoning || data.type === 'reasoning') {
        setMessages(prev => prev.map(m => m.id === thinkingId ? { ...m, think: ((m as any).think || '') + chunk, isThinking: false } : m));
      } else {
        rawContentRef.current += chunk;
        const parsed = parseThink(rawContentRef.current);
        setMessages(prev => prev.map(m => m.id === thinkingId ? { ...m, content: parsed.content, think: parsed.thinking || (m as any).think, isThinking: false } : m));
      }
    } else if (event.event === 'step_result') {
      // Plan Mode：propose_plan 返回的 plan 摘要挂到当前消息，由 PlanProposalCard 渲染
      if (data.tool === 'propose_plan' && data.result?.success && data.result?.plan_id) {
        const plan: PlanProposalPayload = {
          plan_id: data.result.plan_id,
          title: data.result.title,
          summary: data.result.summary,
          step_count: data.result.step_count,
          destructive_steps: data.result.destructive_steps || [],
          steps_preview: data.result.steps_preview || [],
          status: 'pending',
        };
        setMessages(prev => prev.map(m => m.id === thinkingId ? { ...m, plan } : m));
      }
      // Layer auto-mount
      if (data.geojson_ref || data.result?.image) {
        const layerId = `layer-${Date.now()}`;
        const layerName = data.tool === 'search_poi' ? `搜索结果: ${data.name || 'POI'}` :
                         data.tool === 'heatmap_data' ? '热力图分析' : `分析结果: ${data.tool}`;
        const accentColor = useHudStore.getState().accentColor;
        const legendSpec = data.result?.legend_spec ?? undefined;
        const layerMetaTitle: string | null = data.result?.layer_meta?.title ?? null;
        useHudStore.getState().addLayer({
          id: layerId,
          name: layerName,
          type: data.result?.image ? 'heatmap' : 'vector',
          visible: true,
          opacity: 1,
          group: 'analysis',
          source: data.geojson_ref ? { type: 'FeatureCollection', features: [], metadata: { ref_id: data.geojson_ref } } as any : data.result,
          style: { color: accentColor },
          _refId: data.geojson_ref,
          legend_spec: legendSpec,
        });
        if (layerMetaTitle) {
          useHudStore.getState().setCartographyTitle(layerMetaTitle);
        }

        // Asynchronously fetch the actual GeoJSON data for the reference
        if (data.geojson_ref) {
          const sid = sessionIdRef.current;
          fetch(`${API_BASE}/api/v1/layers/data/${data.geojson_ref}?session_id=${sid}`)
            .then(r => r.ok ? r.json() : null)
            .then(geojson => {
              if (geojson && (geojson.type === 'FeatureCollection' || geojson.features)) {
                useHudStore.getState().updateLayer(layerId, { source: geojson });
              }
            })
            .catch(err => console.error('[LiveLayerFetch] Failed to fetch geojson_ref:', err));
        }

        setMessages(prev => prev.map(m => m.id === thinkingId ? { ...m, layerAdded: layerName } : m));
      }
      // NOTE: command dispatch + bbox flyTo are handled by the bridge
    } else if (event.event === 'plan_ready') {
      try {
        const incoming = data;
        setMessages(prev => prev.map(m => m.id === thinkingId ? { ...m,
          agentPlan: {
            intent: incoming.intent,
            domains: incoming.domains ?? [],
            steps: (incoming.steps ?? []).map((s: any) => ({
              n: s.n, goal: s.goal, tool_family: s.tool_family, status: 'pending' as const,
            })),
            finalized: false,
          },
        } : m));
      } catch (err) { console.warn('[plan_ready] parse failed', err); }
    } else if (event.event === 'plan_step_done') {
      try {
        const stepN = data.step_n;
        setMessages(prev => prev.map(m => {
          if (m.id !== thinkingId || !m.agentPlan) return m;
          return { ...m, agentPlan: { ...m.agentPlan,
            steps: m.agentPlan.steps.map(s => s.n === stepN ? { ...s, status: 'done' as const } : s),
          }};
        }));
      } catch (err) { console.warn('[plan_step_done] parse failed', err); }
    } else if (event.event === 'plan_finalized') {
      try {
        const skipped = new Set<number>(data.skipped ?? []);
        setMessages(prev => prev.map(m => {
          if (m.id !== thinkingId || !m.agentPlan) return m;
          return { ...m, agentPlan: { ...m.agentPlan,
            finalized: true,
            steps: m.agentPlan.steps.map(s =>
              skipped.has(s.n) ? { ...s, status: 'skipped' as const } : s),
          }};
        }));
      } catch (err) { console.warn('[plan_finalized] parse failed', err); }
    } else if (event.event === 'error' || event.event === 'step_error' || event.event === 'task_error') {
      setMessages(prev => prev.map(m => m.id === thinkingId ? { ...m, content: '请求失败，请重试。', isThinking: false } : m));
    } else if (event.event === 'explorer_progress') {
      const taskId = data.task_id as string;
      const stage = data.stage as import('@/lib/types/explorer').ExplorerStage;
      const status = data.status as string;
      const context = (data.context as Record<string, unknown>) || {};
      useHudStore.getState().updateExplorerTask(taskId, {
        stage,
        status: status === 'completed' ? 'completed' :
                status === 'failed' ? 'failed' :
                status === 'decision_point' ? 'decision_required' :
                `${stage}ing` as any,
        progress: (context?.progress as number) || 0,
      });
    }
  }, [parseThink, setMessages]);

  const bridge = useMapBridge(sessionId, dispatchAction, onEvent);
  const isLoading = bridge.aiStatus === 'thinking' || bridge.aiStatus === 'acting';

  /* ─── Plan Mode：审批/修改/取消按钮回调 ─── */
  const handlePlanAction = useCallback(
    (planId: string, action: 'approve' | 'revise' | 'reject') => {
      // 1. 锁住对应卡片，避免重复点击
      setMessages(prev => prev.map(m => (
        m.plan?.plan_id === planId
          ? { ...m, plan: { ...m.plan, status: action === 'approve' ? 'approved' : 'rejected' } }
          : m
      )));
      // 2. 触发一条对应意图的 user message 让 LLM 调 execute_plan / 修改 / 放弃
      const text = action === 'approve'
        ? `执行计划 ${planId}`
        : action === 'revise'
          ? `修改计划 ${planId}（说说哪里需要调整）`
          : `取消计划 ${planId}`;
      // handleSend 在下面声明；用 setTimeout 推迟到下个 tick 以避开闭包顺序问题
      setTimeout(() => handleSendRef.current?.(text), 0);
    },
    []
  );
  const handleSendRef = useRef<((text: string) => void) | null>(null);

  /* ─── Send handler ─── */
  const handleSend = useCallback(
    async (userMsg: string) => {
      if (!userMsg || isLoading) return;

      const { viewport, baseLayer, is3D, layers: hudLayers, selectedFeature } = useHudStore.getState();
      const liveSnapshot = getMapSnapshot();
      const mapState = {
        viewport: {
          center: liveSnapshot?.center ?? viewport.center,
          zoom: liveSnapshot?.zoom ?? viewport.zoom,
          bearing: liveSnapshot?.bearing ?? viewport.bearing ?? 0,
          pitch: liveSnapshot?.pitch ?? viewport.pitch ?? 0,
          bounds: liveSnapshot?.bounds ?? viewport.bounds ?? undefined,
        },
        base_layer: baseLayer,
        is_3d: is3D,
        layers: hudLayers.map((l: any) => ({
          id: l.id,
          name: l.name,
          type: l.type,
          visible: l.visible,
          opacity: l.opacity,
          group: l.group,
          featureCount: l.source && typeof l.source === 'object' && 'features' in l.source
            ? (l.source as any).features?.length ?? 0 : undefined,
          style: l.style,
        })),
        user_location: userLocation ? { lng: userLocation.lng, lat: userLocation.lat, accuracy: userLocation.accuracy } : null,
        selected_feature: selectedFeature ? {
          layer_id: selectedFeature.layerId,
          layer_name: selectedFeature.layerName ?? null,
          ref_id: selectedFeature.refId ?? null,
          point: selectedFeature.point,
          properties: selectedFeature.properties,
          selected_at: selectedFeature.selectedAt,
        } : null,
      };

      setMessages(prev => [...prev, { id: Date.now().toString(), role: 'user' as const, content: userMsg, timestamp: new Date() }]);

      const thinkingMsgId = (Date.now() + 1).toString();
      thinkingMsgIdRef.current = thinkingMsgId;
      rawContentRef.current = '';
      setMessages(prev => [...prev, { id: thinkingMsgId, role: 'assistant' as const, content: '', timestamp: new Date(), isThinking: true }]);

      await bridge.send(userMsg, mapState);

      setMessages(prev => prev.map(m => m.id === thinkingMsgId && (m as any).isThinking ? { ...m, isThinking: false, content: (m as any).content || '完成。' } : m));
    },
    [isLoading, bridge.send, getMapSnapshot, userLocation]
  );

  // 把 handleSend 同步到 ref，让 handlePlanAction 在不参与 deps 的前提下调到最新版本
  useEffect(() => { handleSendRef.current = handleSend; }, [handleSend]);

  /* ─── Main render ─── */
  const aiStatus = useHudStore((s) => s.aiStatus);
  const theme = useHudStore((s) => s.theme);
  // 用响应式 selector 读 accentColor —— 之前在 JSX 里直接 getState() 不订阅 store，
  // 导致用户改主题色后 LeftSidebar / HistoryDrawer 不重渲染（审计 F8）。
  const reactiveAccentColor = useHudStore((s) => s.accentColor);
  const fontSize = useHudStore((s) => s.fontSize);
  const sidebarWidth = useHudStore((s) => s.sidebarWidth);
  const colors = getThemeColors(theme);

  // Get current session title for TopBar
  const currentSessionTitle = sessionId
    ? sessions.find(s => s.id === sessionId)?.title || '新会话'
    : '新会话';

  return (
    <div style={{ height: '100vh', width: '100vw', display: 'flex', flexDirection: 'column', overflow: 'hidden', background: colors.bg, fontSize: `${fontSize}px` }}>
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
        {layers.find(l => l.visible && l.type === 'heatmap') && (
          <div style={{
            position: 'absolute',
            bottom: hudOpen ? 220 : 34,
            left: leftPanelOpen ? sidebarWidth + 14 : 10,
            transition: 'left 0.22s cubic-bezier(0.4,0,0.2,1), bottom 0.3s cubic-bezier(0.4,0,0.2,1)',
            zIndex: 10
          }}>
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
        <div style={{
          position: 'absolute',
          bottom: 30,
          right: 12,
          fontSize: '9.5px',
          color: theme === 'dark' ? 'rgba(148,163,184,0.6)' : 'rgba(15,23,42,0.35)',
          fontFamily: "'JetBrains Mono', monospace",
          background: theme === 'dark' ? 'rgba(30,41,59,0.72)' : 'rgba(255,255,255,0.72)',
          padding: '2px 8px',
          borderRadius: 4,
          backdropFilter: 'blur(8px)',
          WebkitBackdropFilter: 'blur(8px)',
          zIndex: 10,
        }}>
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
