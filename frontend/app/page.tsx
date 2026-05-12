'use client';
import { useState, useCallback, useEffect, useRef } from 'react';
import dynamic from 'next/dynamic';
import { useHudStore, DEMO_LAYERS } from '@/lib/store/useHudStore';
import { getThemeColors } from '@/lib/theme';
import { streamChat } from '@/lib/api/chat';
import { useWebSocket } from '@/lib/hooks/use-websocket';
import { useGeolocation } from '@/lib/hooks/use-geolocation';
import type { ChatSession } from '@/lib/types/chat';
import { API_BASE } from '@/lib/api/config';
import { useMapAction } from '@/lib/contexts/map-action-context';

// New layout components
import TopBar from '@/components/layout/top-bar';
import StatusBar from '@/components/layout/status-bar';
import { LeftSidebar } from '@/components/sidebar/left-sidebar';
import MapToolbar from '@/components/map/map-toolbar';
import AITracker from '@/components/map/ai-tracker';
import BaselayerSwitcher from '@/components/map/baselayer-switcher';
import FloatingLegend from '@/components/map/floating-legend';
import MapCanvas from '@/components/map/map-canvas';
import AgentEnvHud from '@/components/hud/agent-env-hud';
import RagIndependentPanel from '@/components/panel/rag-independent-panel';
import PerceptionRings from '@/components/overlays/perception-rings';
import TweaksPanel from '@/components/tweaks-panel';
import { HistoryDrawer } from '@/components/drawers/history-drawer';
import { SettingsPanel } from '@/components/settings/settings-panel';

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

export default function Home() {
  const { getMapSnapshot } = useMapAction();
  const {
    layers,
    addLayer,
    removeLayer,
    toggleLayer,
    clearLayers,
    setLayers,
    analysisResult,
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
    demoMode,
    setDemoMode,
    setRagResults,
    setExports,
    pushOpLog,
    clearOpsLog,
    pushCausalEntry,
    clearCausalChain,
    setAiStatus,
    setSessions: setStoreSessions,
    taskStart,
    stepStart,
    stepResult,
    taskComplete,
  } = useHudStore();

  /* ─── Chat state ─── */
  const [messages, setMessages] = useState<Array<{ id: string; role: 'user' | 'assistant'; content: string; timestamp: any; isThinking?: boolean; charts?: unknown[]; toolCalls?: ToolCallEntry[] }>>([
    {
      id: '1',
      role: 'assistant',
      content: '你好！我是 GeoAgent。\n\n我感知地图、分析空间、生成洞察——地图上的一切都是我的一部分。\n\n试着告诉我：\n- 分析北京市学校分布密度\n- 成都市人口热力图\n- 计算各区 POI覆盖率',
      timestamp: new Date(),
    },
  ]);
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string>();
  const abortControllerRef = useRef<AbortController | null>(null);

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

  const handleSelectSession = useCallback(async (sid: string) => {
    abortControllerRef.current?.abort();
    clearLayers();
    try {
      const res = await fetch(`${API_BASE}/api/v1/chat/sessions/${sid}`);
      const data = await res.json();
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

      const stateRes = await fetch(`${API_BASE}/api/v1/chat/sessions/${sid}/map-state`);
      if (stateRes.ok) {
        const stateData = await stateRes.json();
        const state = stateData?.map_state;
        if (state) {
          const store = useHudStore.getState();
          if (state.viewport) {
            store.setViewport(state.viewport.center, state.viewport.zoom, state.viewport.bearing, state.viewport.pitch);
          }
          if (state.base_layer) store.setBaseLayer(state.base_layer);
          for (const layer of state.layers || []) {
            if (layer._refId && layer._refId.startsWith('ref:')) {
              fetch(`${API_BASE}/api/v1/layers/data/${layer._refId}?session_id=${sid}`)
                .then(r => r.ok ? r.json() : null)
                .then(geojson => {
                  if (geojson && (geojson.type === 'FeatureCollection' || geojson.features)) {
                    store.addLayer({ ...layer, source: geojson });
                  }
                })
                .catch(() => {});
            }
          }
        }
      }
    } catch (err) {
      console.error('Load session failed:', err);
    }
  }, [setHistoryOpen, clearLayers]);

  const handleNewSession = useCallback(() => {
    abortControllerRef.current?.abort();
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

  useEffect(() => {
    return () => { abortControllerRef.current?.abort(); };
  }, []);

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
            time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
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
      time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
      detail: '保存为 PNG',
    });
    setExports([
      { id: Date.now().toString(), name: '地图快照', type: 'png', size: '1.2 MB', date: '刚刚' },
      ...exports,
    ]);
  }, []);

  /* ─── Simulate run handler (for demo) ─── */
  const simulateRun = useCallback(async (userMsg: string) => {
    const aiStatus = useHudStore.getState().aiStatus;
    if (aiStatus === 'thinking' || aiStatus === 'acting') return;
    setAiStatus('thinking');

    // Add user message
    const userMsgObj = { id: Date.now().toString(), role: 'user' as const, content: userMsg, timestamp: new Date() };
    setMessages(prev => [...prev, userMsgObj]);

    // Add thinking message
    const thinkId = (Date.now() + 1).toString();
    setMessages(prev => [...prev, { id: thinkId, role: 'assistant', content: '', timestamp: new Date(), isThinking: true, toolCalls: [] }]);

    // Simulate adding layers, ops log, causal chain
    taskStart(Date.now().toString());

    // Step 1: geocode
    stepStart(Date.now().toString(), 'step1', 0, 'geocode_cn');
    await new Promise(r => setTimeout(r, 800));
    stepResult(Date.now().toString(), 'step1', 'geocode_cn', { area: '北京市', center: [116.4074, 39.9042] }, false);
    pushOpLog({
      id: Date.now().toString(),
      type: 'flyto',
      label: '飞到 — 目标区域',
      time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
      detail: 'zoom 11.5'
    });
    pushCausalEntry({
      id: Date.now().toString(),
      tool: 'geocode_cn',
      mapAction: 'fly_to',
      time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
      toolInput: '北京市',
      mapEffect: '地图飞至目标位置',
      mapState: { center: [116.40, 39.90], zoom: 10 }
    });

    // Step 2: query POI
    stepStart(Date.now().toString(), 'step2', 1, 'query_osm_poi');
    await new Promise(r => setTimeout(r, 1200));
    // Add POI layer
    const poiLayer = {
      id: `layer-${Date.now()}`,
      name: '北京市学校 POI',
      type: 'vector' as const,
      visible: true,
      opacity: 1,
      color: '#16a34a',
      group: 'analysis',
      info: '123 个要素',
      mockPoints: [[22,18],[28,22],[35,28],[42,15],[50,32],[55,25],[60,18],[38,42],[46,38],[62,45]],
      style: { color: '#16a34a' },
      source: { type: 'FeatureCollection', features: [] } as any
    };
    addLayer(poiLayer as any);
    stepResult(Date.now().toString(), 'step2', 'query_osm_poi', { type: 'poi_query', area: '北京市', category: '学校' }, true);
    pushOpLog({
      id: Date.now().toString(),
      type: 'add',
      label: '添加图层 — POI查询结果',
      time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
      detail: '123 个要素'
    });
    pushCausalEntry({
      id: Date.now().toString(),
      tool: 'query_osm_poi',
      mapAction: 'add_layer',
      time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
      toolInput: 'category=school',
      mapEffect: '新增POI图层'
    });

    // Step 3: KDE
    stepStart(Date.now().toString(), 'step3', 2, 'kde_surface');
    await new Promise(r => setTimeout(r, 2100));
    // Add heatmap layer
    const heatLayer = {
      id: `heat-${Date.now()}`,
      name: '密度热力图',
      type: 'heatmap' as const,
      visible: true,
      opacity: 0.9,
      color: '#ff5f00',
      group: 'analysis',
      info: '核密度估计',
      mockPoints: [[30,25],[35,30],[40,28],[38,22],[33,20],[45,35],[50,28],[44,22]],
      style: { color: '#ff5f00' },
      source: { type: 'FeatureCollection', features: [] } as any
    };
    addLayer(heatLayer as any);
    stepResult(Date.now().toString(), 'step3', 'kde_surface', { type: 'analysis', render_type: 'heatmap' }, true);
    pushOpLog({
      id: Date.now().toString(),
      type: 'add',
      label: '添加图层 — 密度热力图',
      time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
      detail: 'kde_surface 输出'
    });
    pushCausalEntry({
      id: Date.now().toString(),
      tool: 'kde_surface',
      mapAction: 'add_layer',
      time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
      toolInput: 'bandwidth=500m',
      mapEffect: '新增热力图图层'
    });

    // Set RAG results
    setRagResults([
      { id: '1', source: 'GIS空间分析方法论.pdf', score: '0.92', chunks: 4, excerpts: ['核密度估计是一种非参数方法，用于估计随机变量的概率密度函数。在GIS中，常用于分析点要素的空间分布密度。', '带宽选择是KDE的关键参数，过小会造成过拟合，过大则会掩盖局部模式。'] },
      { id: '2', source: '北京市空间数据手册v3.md', score: '0.87', chunks: 2, excerpts: ['北京市共辖16个区，总面积16410平方公里。核心区包括东城区和西城区。'] },
    ]);

    // Set exports
    setExports([
      { id: '1', name: '北京学校密度专题图.png', type: 'png', size: '2.4 MB', date: '刚刚' },
      { id: '2', name: '核密度分析报告.pdf', type: 'pdf', size: '840 KB', date: '刚刚' },
      { id: '3', name: '学校POI数据.geojson', type: 'geojson', size: '156 KB', date: '刚刚' },
    ]);

    // Final response
    await new Promise(r => setTimeout(r, 500));
    const finalContent = '分析完成！已为你生成学校分布热力图。主要发现：核心城区密度较高，东部区域分布较为均衡。';
    setMessages(prev => prev.map(m => m.id === thinkId ? { ...m, content: finalContent, isThinking: false } : m));

    taskComplete(Date.now().toString(), 3, '分析完成');
    setAiStatus('done');
    setTimeout(() => setAiStatus('idle'), 1500);
  }, [taskStart, stepStart, stepResult, taskComplete, addLayer, pushOpLog, pushCausalEntry, setRagResults, setExports, setAiStatus]);

  /* ─── Real send handler ─── */
  const handleSend = useCallback(
    async (userMsg: string) => {
      if (!userMsg || isLoading) return;

      // If demo mode, use simulation
      if (demoMode) {
        return simulateRun(userMsg);
      }

      abortControllerRef.current?.abort();
      abortControllerRef.current = new AbortController();
      const currentSignal = abortControllerRef.current.signal;

      const { viewport, baseLayer, is3D, layers: hudLayers } = useHudStore.getState();
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
      };

      const userMessageObj = { id: Date.now().toString(), role: 'user' as const, content: userMsg, timestamp: new Date() };
      setMessages(prev => [...prev, userMessageObj]);
      setIsLoading(true);

      const thinkingMsg = { id: (Date.now() + 1).toString(), role: 'assistant' as const, content: '', timestamp: new Date(), isThinking: true };
      setMessages(prev => [...prev, thinkingMsg]);

      try {
        let assistantContent = '';
        let assistantThinking = '';
        setAiStatus('thinking');

        for await (const event of streamChat(userMsg, sessionId, mapState, currentSignal)) {
          const { event: eventType, data: dataRaw } = event;
          const data = dataRaw as any;

          if (data?.session_id && data.session_id !== sessionId) {
            setSessionId(data.session_id);
          }

          if (['thinking', 'planning', 'step_start'].includes(eventType)) {
            setAiStatus('thinking');
          } else if (['acting', 'observing', 'tool_call'].includes(eventType)) {
            setAiStatus('acting');
          }

          if (eventType === 'token') {
            const chunk = data.content || '';
            // 简单的启发式识别：如果内容包含大量推理标记或在工具调用前，归类为思考
            // 实际上后端已经修正了，如果是推理令牌，eventType 依然是 token 但内容不同
            // 这里我们根据后端发送的字段来区分
            if (data.is_reasoning || data.type === 'reasoning') {
              assistantThinking += chunk;
            } else {
              assistantContent += chunk;
            }
            setMessages(prev => prev.map(m => m.id === thinkingMsg.id ? { 
              ...m, 
              content: assistantContent, 
              think: assistantThinking,
              isThinking: false 
            } : m));
          } else if (eventType === 'content') {
            const chunk = data.content || '';
            assistantContent += chunk;
            setMessages(prev => prev.map(m => m.id === thinkingMsg.id ? { ...m, content: assistantContent, isThinking: false } : m));
          } else if (eventType === 'step_result') {
            // 1. 自动图层挂载
            if (data.geojson_ref || data.result?.image) {
              const layerId = `layer-${Date.now()}`;
              const layerName = data.tool === 'search_poi' ? `搜索结果: ${data.name || 'POI'}` : 
                               data.tool === 'heatmap_data' ? '热力图分析' : `分析结果: ${data.tool}`;
              
              useHudStore.getState().addLayer({
                id: layerId,
                name: layerName,
                type: data.result?.image ? 'heatmap' : 'vector',
                visible: true,
                opacity: 1,
                color: colors.accent,
                group: 'analysis',
                source: data.geojson_ref ? { type: 'FeatureCollection', features: [], metadata: { ref_id: data.geojson_ref } } as any : data.result,
                style: { color: colors.accent }
              });
              
              setMessages(prev => prev.map(m => m.id === thinkingMsg.id ? { ...m, layerAdded: layerName } : m));
            }

            // 2. 自动缩放 (bbox)
            const bbox = data.result?.bbox || data.bbox;
            if (bbox) {
              // bbox 格式: [west, south, east, north]
              const center: [number, number] = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2];
              // 粗略计算 zoom
              const latDiff = Math.abs(bbox[3] - bbox[1]);
              const lonDiff = Math.abs(bbox[2] - bbox[0]);
              const maxDiff = Math.max(latDiff, lonDiff);
              const zoom = maxDiff > 10 ? 4 : maxDiff > 1 ? 8 : maxDiff > 0.1 ? 11 : 14;
              
              // 使用 setAnalysisResult 触发 MapPanel 的 useEffect flyTo
              useHudStore.getState().setAnalysisResult({
                center,
                zoom,
                success: true
              });
            }
          } else if (eventType === 'task_complete') {
            setAiStatus('done');
            if (data.summary) {
              // 可以将 summary 追加到消息或更新状态
            }
          } else if (eventType === 'explorer_progress') {
            const expData = data as any;
            const taskId = expData.task_id as string;
            const stage = expData.stage as string;
            const status = expData.status as string;
            const context = expData.context as Record<string, unknown> || {};
            useHudStore.getState().updateExplorerTask(taskId, {
              stage,
              status: status === 'completed' ? 'completed' :
                      status === 'failed' ? 'failed' :
                      status === 'decision_point' ? 'decision_required' :
                      `${stage}ing` as any,
              progress: (context?.progress as number) || 0,
            });
          } else if (eventType === 'done' || eventType === 'end') {
            break;
          }
        }
      } catch {
        setAiStatus('error');
        setMessages(prev => prev.map(m => m.id === thinkingMsg.id ? { ...m, content: '请求失败，请重试。', isThinking: false } : m));
      } finally {
        setAiStatus('done');
        setTimeout(() => setAiStatus('idle'), 1500);
        setIsLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isLoading, demoMode, simulateRun, getMapSnapshot, userLocation, setAiStatus, sessionId]
  );

  /* ─── Main render ─── */
  const aiStatus = useHudStore((s) => s.aiStatus);
  const theme = useHudStore((s) => s.theme);
  const fontSize = useHudStore((s) => s.fontSize);
  const showGrid = useHudStore((s) => s.showGrid);
  const setLeftTab = useHudStore((s) => s.setActiveLeftTab);
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
        onNewSession={() => {
          if (!demoMode) {
            handleNewSession();
          } else {
            setDemoMode(false);
            handleNewSession();
          }
        }}
      />

      <div style={{ flex: 1, position: 'relative', overflow: 'hidden', marginTop: 42, marginBottom: 24 }}>
        {/* Map Canvas or Real Map Panel */}
        {demoMode ? (
          <MapCanvas showGrid={showGrid}>
            {/* Layer dots for demo */}
            {layers.filter(l => l.visible && (l as any).mockPoints).map((layer, lidx) =>
              ((layer as any).mockPoints as [number, number][]).map((pt, pidx) => (
                <div
                  key={`${lidx}-${pidx}`}
                  style={{
                    position: 'absolute',
                    left: `${pt[0]}%`,
                    top: `${pt[1]}%`,
                    width: layer.type === 'heatmap' ? 28 : 9,
                    height: layer.type === 'heatmap' ? 28 : 9,
                    borderRadius: '50%',
                    background: layer.type === 'heatmap' ? `radial-gradient(circle, ${(layer as any).color || '#ff5f00'}88 0%, transparent 70%)` : ((layer as any).color || '#16a34a'),
                    transform: 'translate(-50%, -50%)',
                    pointerEvents: 'none',
                    boxShadow: `0 0 ${layer.type === 'heatmap' ? 18 : 4}px ${(layer as any).color || '#16a34a'}55`,
                  }}
                />
              ))
            )}

            <PerceptionRings active={aiStatus === 'thinking' || aiStatus === 'acting'} />
          </MapCanvas>
        ) : (
          <div style={{ position: 'absolute', inset: 0 }}>
            <MapPanel
              layers={layers}
              onRemoveLayer={removeLayer}
              onToggleLayer={toggleLayer}
              analysisResult={analysisResult}
            />
          </div>
        )}

        {/* Base layer switcher */}
        <div style={{ position: 'absolute', bottom: 34, right: hudOpen ? 346 : 56, zIndex: 15, transition: 'right 0.22s cubic-bezier(0.4,0,0.2,1)' }}>
          <BaselayerSwitcher />
        </div>

        {/* Floating Legend */}
        {layers.find(l => l.visible && l.type === 'heatmap') && (
          <div style={{ position: 'absolute', bottom: 34, left: leftPanelOpen ? sidebarWidth + 14 : 10, transition: 'left 0.22s cubic-bezier(0.4,0,0.2,1)', zIndex: 10 }}>
            <FloatingLegend />
          </div>
        )}

        {/* Left Sidebar */}
        <LeftSidebar
          open={leftPanelOpen}
          messages={messages}
          aiStatus={aiStatus}
          onSend={demoMode ? simulateRun : handleSend}
          accentColor={useHudStore.getState().accentColor}
        />

        {/* Map Toolbar */}
        <MapToolbar
          hudOpen={hudOpen}
          onToggleHud={() => setHudOpen(!hudOpen)}
          onZoomIn={handleZoomIn}
          onZoomOut={handleZoomOut}
          onHome={handleHome}
          onLocate={handleLocate}
          onExport={handleExport}
        />

        {/* AI Tracker */}
        <AITracker />

        {/* Agent HUD */}
        <AgentEnvHud open={hudOpen} onClose={() => setHudOpen(false)} />

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

      <StatusBar />

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
        accentColor={useHudStore.getState().accentColor}
      />

      {settingsOpen && <SettingsPanel />}

      {/* Tweaks Panel Wrapper */}
      <TweaksPanel />

      {/* Tweaks Toggle Button */}
      <button
        onClick={() => setTweaksOpen(!tweaksOpen)}
        style={{
          position: 'fixed',
          bottom: 40,
          left: '50%',
          transform: 'translateX(-50%)',
          zIndex: 99,
          opacity: 0.3,
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          padding: 4,
          fontSize: '16px',
        }}
        title='Toggle UI adjustments'
      >
        ⚙
      </button>

      {/* Demo Mode Toggle */}
      {!demoMode && (
        <button
          onClick={() => {
            setDemoMode(true);
            setLayers(DEMO_LAYERS as any);
            setLeftTab('chat');
          }}
          style={{
            position: 'fixed',
            bottom: 40,
            left: 20,
            zIndex: 99,
            fontSize: '10px',
            padding: '4px 8px',
            borderRadius: 6,
            border: '1px solid rgba(15,23,42,0.1)',
            background: 'rgba(255,255,255,0.8)',
            cursor: 'pointer',
            color: '#475569',
          }}
        >
          Try Demo
        </button>
      )}
      {demoMode && (
        <button
          onClick={() => {
            setDemoMode(false);
            clearLayers();
            clearOpsLog();
            clearCausalChain();
            setRagResults([]);
            setExports([]);
          }}
          style={{
            position: 'fixed',
            bottom: 40,
            left: 20,
            zIndex: 99,
            fontSize: '10px',
            padding: '4px 8px',
            borderRadius: 6,
            border: '1px solid rgba(163,74,74,0.3)',
            background: 'rgba(255,230,230,0.9)',
            cursor: 'pointer',
            color: '#991b1b',
          }}
        >
          Exit Demo
        </button>
      )}
    </div>
  );
}
