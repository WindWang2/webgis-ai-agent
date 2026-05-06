'use client';
import { useState, useCallback, useEffect, useRef } from 'react';
import dynamic from 'next/dynamic';
import { useHudStore, DEMO_OPS_LOG, DEMO_RAG, DEMO_EXPORTS, DEMO_CAUSAL_CHAIN, DEMO_LAYERS } from '@/lib/store/useHudStore';
import { streamChat } from '@/lib/api/chat';
import { useWebSocket } from '@/lib/hooks/use-websocket';
import { useGeolocation } from '@/lib/hooks/use-geolocation';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import type { ChatSession } from '@/lib/types/chat';
import type { UploadResponse } from '@/lib/api/upload';
import { getUploadGeojson } from '@/lib/api/upload';
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

function computeBBoxFromFeatures(features: any[]): [number, number, number, number] | undefined {
  if (!features || features.length === 0) return undefined;
  let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
  const collect = (coords: number[][]) => {
    for (const c of coords) { minLng = Math.min(minLng, c[0]); maxLng = Math.max(maxLng, c[0]); minLat = Math.min(minLat, c[1]); maxLat = Math.max(maxLat, c[1]); }
  };
  for (const f of features) {
    const g = f.geometry;
    if (!g?.coordinates) continue;
    switch (g.type) {
      case 'Point': { minLng = Math.min(minLng, g.coordinates[0]); maxLng = Math.max(maxLng, g.coordinates[0]); minLat = Math.min(minLat, g.coordinates[1]); maxLat = Math.max(maxLat, g.coordinates[1]); break; }
      case 'MultiPoint': case 'LineString': collect(g.coordinates); break;
      case 'MultiLineString': g.coordinates.forEach((r: number[][]) => collect(r)); break;
      case 'Polygon': collect(g.coordinates[0] || []); break;
      case 'MultiPolygon': g.coordinates.forEach((p: number[][][]) => collect(p[0] || [])); break;
    }
  }
  if (minLng === Infinity) return undefined;
  return [minLng, minLat, maxLng, maxLat];
}

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
  const { dispatchAction, getMapSnapshot } = useMapAction();
  const {
    layers,
    addLayer,
    addProcessLayer,
    removeLayer,
    toggleLayer,
    clearLayers,
    setLayers,
    analysisResult,
    setAnalysisResult,
    leftPanelOpen,
    toggleLeftPanel,
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
    stepError,
    taskComplete,
    clearTask,
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

  /* ─── Tool result handler ─── */
  const handleToolResult = useCallback(
    async (toolName: string, result: any, sid?: string) => {
      let geojson = result.geojson;
      let bbox = result.bbox;
      let image = result.image;

      if (!geojson && result.geojson_ref && typeof result.geojson_ref === 'string' && result.geojson_ref.startsWith('ref:') && sid) {
        try {
          const resp = await fetch(`${API_BASE}/api/v1/layers/data/${result.geojson_ref}?session_id=${sid}`);
          if (resp.ok) {
            const fullData = await resp.json();
            if (fullData.type === 'FeatureCollection' || (typeof fullData === 'object' && 'features' in fullData)) {
              geojson = fullData;
            } else if (fullData.image) {
              image = fullData.image;
              bbox = fullData.bbox;
            }
          }
        } catch (e) {
          console.error('Fetch layer data failed:', e);
        }
      }

      if ((result?.type === 'heatmap_raster' || image) && (result?.image || image) && (result.bbox || bbox)) {
        const layerId = `heatmap_raster-${Date.now()}`;
        const finalBbox = bbox || result.bbox;
        const finalImage = image || result.image;
        const [west, south, east, north] = finalBbox;
        const center: [number, number] = [(west + east) / 2, (south + north) / 2];
        setAnalysisResult({ center, zoom: 10 });
        addLayer({
          id: layerId,
          name: '热力图（栅格）',
          type: 'heatmap',
          visible: true,
          opacity: 0.85,
          source: { image: finalImage, bbox: finalBbox },
          style: {},
        });
        return;
      }

      if (geojson && geojson.features?.length > 0) {
        const layerId = `${toolName}-${Date.now()}`;
        const colors = ['#16a34a', '#2563eb', '#ea580c', '#8b5cf6', '#ec4899', '#dc2626'];
        const color = colors[Math.floor(Math.random() * colors.length)];

        const lngs: number[] = [];
        const lats: number[] = [];
        const collectCoords = (coords: number[][]) => {
          coords.forEach((c: number[]) => { lngs.push(c[0]); lats.push(c[1]); });
        };
        const collectFromGeometry = (geometry: any) => {
          const c = geometry.coordinates;
          if (!c) return;
          switch (geometry.type) {
            case 'Point': { const pt = c as number[]; lngs.push(pt[0]); lats.push(pt[1]); break; }
            case 'MultiPoint': collectCoords(c as number[][]); break;
            case 'LineString': collectCoords(c as number[][]); break;
            case 'MultiLineString': (c as unknown as number[][][]).forEach((ring: number[][]) => collectCoords(ring)); break;
            case 'Polygon': collectCoords((c as unknown as number[][][])[0] || []); break;
            case 'MultiPolygon': (c as unknown as number[][][][]).forEach((poly: number[][][]) => collectCoords(poly[0] || [])); break;
          }
        };
        geojson.features.forEach((f: any) => collectFromGeometry(f.geometry));

        let center: [number, number] | undefined;
        let zoom = 12;
        if (lngs.length > 0) {
          center = [lngs.reduce((a: number, b: number) => a + b, 0) / lngs.length, lats.reduce((a: number, b: number) => a + b, 0) / lats.length];
          const count = geojson.features.length;
          if (count > 100) zoom = 10;
          else if (count > 50) zoom = 11;
          else if (count < 10) zoom = 13;
        } else if (bbox || result.bbox) {
          const b = bbox || result.bbox;
          const parts = typeof b === 'string' ? b.split(',').map(Number) : b;
          if (parts.length === 4) {
            center = typeof b === 'string' ? [(parts[1] + parts[3]) / 2, (parts[0] + parts[2]) / 2] : [(parts[0] + parts[2]) / 2, (parts[1] + parts[3]) / 2];
          }
        }

        if (center) setAnalysisResult({ center, zoom });

        if (geojson && geojson.features?.length > 0) {
          const isGrid = geojson?.metadata?.render_type === 'grid';
          const isNative = geojson?.metadata?.render_type === 'native';
          const layerType = isGrid || isNative ? 'heatmap' : 'vector';
          const layerStyle = isGrid ? { color, renderType: 'grid' as const } : isNative ? { color, renderType: 'heatmap' as const } : { color };

          addLayer({
            id: layerId,
            name: isNative ? '原生热力图' : isGrid ? '格网热力分析' : result.type === 'poi_query' ? `${result.area} - ${result.category}` : result.type || toolName,
            type: layerType,
            visible: true,
            opacity: 1,
            group: result.group || 'analysis',
            source: geojson,
            style: layerStyle,
            _refId: result.geojson_ref || undefined,
          });
        }
      }
    },
    [addLayer, setAnalysisResult]
  );

  /* ─── Upload handler (reserved) ─── */
  const _handleUploadSuccess = useCallback(
    async (result: UploadResponse) => {
      if (result.file_type === 'vector' && result.bbox) {
        try {
          const geojson = await getUploadGeojson(result.id);
          if (geojson.features?.length > 0) {
            const layerId = `upload-${result.id}`;
            const colors = ['#16a34a', '#2563eb', '#ea580c', '#8b5cf6', '#ec4899', '#dc2626'];
            const color = colors[result.id % colors.length];
            const [west, south, east, north] = result.bbox;
            const center: [number, number] = [(west + east) / 2, (south + north) / 2];
            const zoom = result.feature_count > 100 ? 10 : result.feature_count > 50 ? 11 : 12;
            setAnalysisResult({ center, zoom });
            addLayer({
              id: layerId,
              name: result.original_name,
              type: 'vector',
              visible: true,
              opacity: 1,
              group: 'reference',
              source: geojson as any,
              style: { color },
            });
            useHudStore.getState().pushPerception('upload_completed', {
              original_name: result.original_name,
              feature_count: result.feature_count,
              layer_id: layerId,
              crs: result.crs,
              file_type: result.file_type,
            });
            setMessages(prev => [...prev, {
              id: `upload-notify-${Date.now()}`,
              role: 'assistant',
              content: `已感知新数据源：「${result.original_name}」（${result.feature_count} 个要素）已挂载到地图。`,
              timestamp: new Date(),
            }]);
          }
        } catch (e) {
          console.error('加载上传数据到地图失败:', e);
        }
      } else if (result.file_type === 'raster' && result.bbox) {
        const [west, south, east, north] = result.bbox;
        const center: [number, number] = [(west + east) / 2, (south + north) / 2];
        setAnalysisResult({ center, zoom: 10 });
      }
    },
    [addLayer, setAnalysisResult]
  );

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
    const { setExports, pushOpLog } = useHudStore.getState();
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
  }, [exports]);

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

    // Simulate steps
    const steps = [
      { tool: 'geocode_cn', duration: 800 },
      { tool: 'query_osm_poi', duration: 1200 },
      { tool: 'kde_surface', duration: 2100 },
    ];

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

      const { viewport, baseLayer, is3D } = useHudStore.getState();
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
        layers: [],
        user_location: userLocation ? { lng: userLocation.lng, lat: userLocation.lat, accuracy: userLocation.accuracy } : null,
      };

      const userMessageObj = { id: Date.now().toString(), role: 'user' as const, content: userMsg, timestamp: new Date() };
      setMessages(prev => [...prev, userMessageObj]);
      setIsLoading(true);

      const thinkingMsg = { id: (Date.now() + 1).toString(), role: 'assistant' as const, content: '', timestamp: new Date(), isThinking: true };
      setMessages(prev => [...prev, thinkingMsg]);

      try {
        let assistantContent = '';
        setAiStatus('thinking');

        for await (const event of streamChat(userMsg, sessionId, mapState, currentSignal)) {
          const { event: eventType, data: dataRaw } = event;
          const data = dataRaw as any;

          if (['thinking', 'planning'].includes(eventType)) {
            setAiStatus('thinking');
          } else if (['acting', 'observing'].includes(eventType)) {
            setAiStatus('acting');
          }

          if (eventType === 'message' || eventType === 'content' || eventType === 'token') {
            const chunk = typeof data === 'object' ? ((data as any).content || (data as any).text || (data as any).message || '') : String(data);
            assistantContent += chunk;
            setMessages(prev => prev.map(m => m.id === thinkingMsg.id ? { ...m, content: assistantContent, isThinking: false } : m));
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
    [isLoading, demoMode, simulateRun, getMapSnapshot, userLocation, setAiStatus]
  );

  /* ─── Main render ─── */
  const aiStatus = useHudStore((s) => s.aiStatus);
  const fontSize = useHudStore((s) => s.fontSize);
  const showGrid = useHudStore((s) => s.showGrid);
  const leftTab = useHudStore((s) => s.activeLeftTab);
  const setLeftTab = useHudStore((s) => s.setActiveLeftTab);

  // Get current session title for TopBar
  const currentSessionTitle = sessionId
    ? sessions.find(s => s.id === sessionId)?.title || '新会话'
    : '新会话';

  return (
    <div style={{ height: '100vh', width: '100vw', display: 'flex', flexDirection: 'column', overflow: 'hidden', background: '#dce8f2', fontSize: `${fontSize}px` }}>
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
          <div style={{ position: 'absolute', bottom: 34, left: leftPanelOpen ? 344 : 10, transition: 'left 0.22s cubic-bezier(0.4,0,0.2,1)', zIndex: 10 }}>
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
          sidebarOpen={leftPanelOpen}
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
          color: 'rgba(15,23,42,0.35)',
          fontFamily: "'JetBrains Mono', monospace",
          background: 'rgba(255,255,255,0.72)',
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
