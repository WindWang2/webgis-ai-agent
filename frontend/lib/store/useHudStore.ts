'use client';

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { API_BASE } from '../api/config';
import type { AiStatus, HudState, LeftTab, SettingsTab } from './hud-types';

export type { HudState, TaskStep, TaskState, AiStatus, LeftTab, SettingsTab } from './hud-types';

const DEFAULT_MCP_SERVERS = [
  { id: 'gdal-raster', name: 'gdal-raster', transport: 'stdio' as const, cmd: 'python mcp_servers/gdal_raster.py', status: 'active' as const, desc: '栅格数据处理（重采样、裁切、投影）' },
  { id: 'spatial-analysis', name: 'spatial-analysis', transport: 'stdio' as const, cmd: 'python mcp_servers/spatial_analysis.py', status: 'active' as const, desc: '空间分析算法库（缓冲区、叠加、统计）' },
  { id: 'gdal-vector', name: 'gdal-vector', transport: 'stdio' as const, cmd: 'python mcp_servers/gdal_vector.py', status: 'active' as const, desc: '矢量数据读写与格式转换' },
  { id: 'gdal-dem-source', name: 'gdal-dem-source', transport: 'stdio' as const, cmd: 'python mcp_servers/gdal_dem_source.py', status: 'inactive' as const, desc: '高程数据源接入（需 OpenTopography Key）', warn: true },
  { id: 'zai-mcp-server', name: 'zai-mcp-server', transport: 'stdio' as const, cmd: 'npx @z_ai/mcp-server', status: 'active' as const, desc: '智谱 AI 模型服务' },
  { id: 'web-search-prime', name: 'web-search-prime', transport: 'sse' as const, url: 'https://open.bigmodel.cn/api/mcp/web_search_prime/sse', status: 'active' as const, desc: '实时网络搜索' },
  { id: 'web-reader', name: 'web-reader', transport: 'sse' as const, url: 'https://open.bigmodel.cn/api/mcp/web_reader/sse', status: 'active' as const, desc: '网页内容读取与解析' },
  { id: 'zread', name: 'zread', transport: 'sse' as const, url: 'https://open.bigmodel.cn/api/mcp/zread/sse', status: 'inactive' as const, desc: '长文档阅读理解' },
];

const DEFAULT_SKILLS = [
  { id: 'poi', name: 'POI 查询', desc: '通过 Overpass 查询兴趣点，支持多种分类', enabled: true, calls: 0, category: '数据获取' },
  { id: 'ndvi', name: 'NDVI 植被分析', desc: '基于遥感影像计算归一化植被指数', enabled: true, calls: 0, category: '遥感分析' },
  { id: 'heatmap', name: '人口密度热力图', desc: '核密度估计 (KDE) 生成连续密度表面', enabled: true, calls: 0, category: '空间分析' },
  { id: 'buffer', name: '缓冲区分析', desc: '生成点线面的等距缓冲区', enabled: true, calls: 0, category: '空间分析' },
  { id: 'network', name: '路网分析', desc: '最短路径、服务区、行驶时间等图论分析', enabled: false, calls: 0, category: '网络分析' },
  { id: 'overlay', name: '叠加分析', desc: '空间相交、联合、裁切等拓扑运算', enabled: true, calls: 0, category: '空间分析' },
  { id: 'viewshed', name: '可视域分析', desc: '基于 DEM 计算观测点可见范围', enabled: false, calls: 0, category: '地形分析' },
  { id: 'grid', name: '格网统计', desc: '渔网格网生成与属性聚合统计', enabled: true, calls: 0, category: '空间分析' },
  { id: 'report', name: '报告生成', desc: '一键生成带图表的 PDF/HTML 分析报告', enabled: true, calls: 0, category: '输出' },
  { id: 'choropleth', name: '专题地图', desc: '分位数、自然断点等方法的分级设色', enabled: true, calls: 0, category: '制图' },
];

const DEFAULT_MAP_STYLES = [
  { id: 0, name: 'OSM Voyager', desc: '清晰浅色地图' },
  { id: 1, name: 'OSM Dark', desc: '深色街道地图' },
  { id: 2, name: 'Satellite', desc: '卫星影像底图' },
  { id: 3, name: 'Topo', desc: '地形晕渲底图' },
  { id: 4, name: 'Blank White', desc: '空白白色画布' },
];

export const useHudStore = create<HudState>()(
  persist(
    (set, get) => ({
      /* ─── Layers ─── */
      layers: [],
      addLayer: (layer) => set((s) => ({ layers: [layer, ...s.layers] })),
      removeLayer: (id) => set((s) => ({ layers: s.layers.filter((l) => l.id !== id) })),
      toggleLayer: (id) =>
        set((s) => ({
          layers: s.layers.map((l) => (l.id === id ? { ...l, visible: !l.visible } : l)),
        })),
      updateLayer: (id, updates) =>
        set((s) => ({
          layers: s.layers.map((l) => (l.id === id ? { ...l, ...updates } : l)),
        })),
      reorderLayers: (layers) => set({ layers }),
      clearLayers: () => set({ layers: [] }),

      /* ─── Layer Editing ─── */
      editingLayerId: null,
      setEditingLayerId: (id) => set({ editingLayerId: id }),

      /* ─── Analysis ─── */
      analysisResult: null,
      setAnalysisResult: (result) => set({ analysisResult: result }),

      /* ─── Task ─── */
      currentTask: null,

      taskStart: (taskId) =>
        set({ currentTask: { id: taskId, steps: [], status: 'running' } }),

      stepStart: (taskId, stepId, stepIndex, tool) =>
        set((s) => {
          if (!s.currentTask || s.currentTask.id !== taskId) return s;
          return {
            currentTask: {
              ...s.currentTask,
              steps: [
                ...s.currentTask.steps,
                { id: stepId, tool, stepIndex, status: 'running', startedAt: Date.now() },
              ],
            },
          };
        }),

      stepResult: (taskId, stepId, tool, result, hasGeojson, snapshot) =>
        set((s) => {
          if (!s.currentTask || s.currentTask.id !== taskId) return s;
          return {
            currentTask: {
              ...s.currentTask,
              steps: s.currentTask.steps.map((step) =>
                step.id === stepId
                  ? { ...step, status: 'completed' as const, result, hasGeojson, tool, geojsonSnapshot: snapshot, completedAt: Date.now() }
                  : step
              ),
            },
          };
        }),

      stepError: (taskId, stepId, error) =>
        set((s) => {
          if (!s.currentTask || s.currentTask.id !== taskId) return s;
          return {
            currentTask: {
              ...s.currentTask,
              steps: s.currentTask.steps.map((step) =>
                step.id === stepId ? { ...step, status: 'failed' as const, error, completedAt: Date.now() } : step
              ),
            },
          };
        }),

      taskComplete: (taskId, stepCount, summary) =>
        set((s) => {
          if (!s.currentTask || s.currentTask.id !== taskId) return s;
          return {
            currentTask: { ...s.currentTask, status: 'completed', stepCount, summary },
          };
        }),

      taskError: (taskId, error) =>
        set((s) => {
          if (!s.currentTask || s.currentTask.id !== taskId) return s;
          return {
            currentTask: { ...s.currentTask, status: 'failed', summary: error },
          };
        }),

      taskCancelled: (taskId) =>
        set((s) => {
          if (!s.currentTask || s.currentTask.id !== taskId) return s;
          return { currentTask: { ...s.currentTask, status: 'cancelled' } };
        }),

      clearTask: () => set({ currentTask: null }),

      /* ─── Process Layers ─── */
      processLayers: {},
      addProcessLayer: (stepId, geojson) =>
        set((s) => ({ processLayers: { ...s.processLayers, [stepId]: geojson } })),
      removeProcessLayer: (stepId) =>
        set((s) => {
          const { [stepId]: _removed, ...rest } = s.processLayers;
          void _removed;
          return { processLayers: rest };
        }),
      clearProcessLayers: () => set({ processLayers: {} }),

      /* ─── HUD Panels (legacy compat) ─── */
      leftPanelOpen: true,
      rightPanelOpen: true,
      toggleLeftPanel: () => set((s) => ({ leftPanelOpen: !s.leftPanelOpen })),
      toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),

      /* ─── RAG ─── */
      ragInsight: null,
      setRagInsight: (insight) => set({ ragInsight: insight }),

      /* ─── Viewport Sync ─── */
      viewport: { center: [116.4074, 39.9042], zoom: 4, bearing: 0, pitch: 0, bounds: undefined },
      setViewport: (center, zoom, bearing, pitch, bounds) =>
        set({ viewport: { center, zoom, bearing: bearing ?? 0, pitch: pitch ?? 0, bounds } }),
      baseLayer: 'Carto Light',
      setBaseLayer: (name) => set({ baseLayer: name }),
      is3D: false,
      setIs3D: (v: boolean) => set({ is3D: v }),

      /* ─── Perception Buffer ─── */
      _perceptionQueue: [],
      pushPerception: (event: string, data: Record<string, unknown>) =>
        set((s) => ({ _perceptionQueue: [...s._perceptionQueue, { event, data }] })),
      drainPerception: () => {
        const queue = get()._perceptionQueue;
        if (queue.length === 0) return [];
        set({ _perceptionQueue: [] });
        return queue;
      },

      /* ─── System Callback ─── */
      pendingSystemMessage: null,
      setPendingSystemMessage: (msg: string | null) => set({ pendingSystemMessage: msg }),

      /* ─── Analysis Assets ─── */
      analysisAssets: [],
      fetchAnalysisAssets: async (sessionId: string | undefined) => {
        try {
          const resp = await fetch(`${API_BASE}/api/v1/uploads?session_id=${sessionId || ''}`);
          if (resp.ok) {
            const data = await resp.json();
            const assets = data.uploads.filter((u: any) => u.geometry_type === "raster_analysis");
            set({ analysisAssets: assets });
          }
        } catch (e) {
          console.error("Failed to fetch assets:", e);
        }
      },
      updateAsset: (id: number | string, updates: Record<string, unknown>) => set((s) => ({
        analysisAssets: s.analysisAssets.map((a) => a.id === id ? { ...a, ...updates } : a)
      })),
      deleteAsset: (id: number | string) => set((s) => ({
        analysisAssets: s.analysisAssets.filter((a) => a.id !== id)
      })),

      /* ─── Settings (legacy compat) ─── */
      settingsOpen: false,
      setSettingsOpen: (open: boolean) => set({ settingsOpen: open }),
      mcpConfig: "{}",
      setMcpConfig: (config: string) => set({ mcpConfig: config }),
      llmConfig: {},
      setLlmConfig: (config: Record<string, unknown>) => set({ llmConfig: config }),
      availableSkills: [],
      setAvailableSkills: (skills: any[]) => set({ availableSkills: skills }),

      /* ─── Agent UI State ─── */
      aiStatus: 'idle' as AiStatus,
      setAiStatus: (status: AiStatus) => set({ aiStatus: status }),
      activeLeftTab: 'chat' as LeftTab,
      setActiveLeftTab: (tab: LeftTab) => set({ activeLeftTab: tab }),
      historyOpen: false,
      setHistoryOpen: (open: boolean) => set({ historyOpen: open }),
      settingsTab: 'llm' as SettingsTab,
      setSettingsTab: (tab: SettingsTab) => set({ settingsTab: tab }),
      sessions: [],
      setSessions: (sessions) => set({ sessions }),

      /* ─── Settings Data (persisted) ─── */
      mcpServers: DEFAULT_MCP_SERVERS,
      setMcpServers: (servers) => set({ mcpServers: servers }),
      toggleMcpServer: (id: string) =>
        set((s) => ({
          mcpServers: s.mcpServers.map((srv) =>
            srv.id === id ? { ...srv, status: srv.status === 'active' ? 'inactive' as const : 'active' as const } : srv
          ),
        })),
      skills: DEFAULT_SKILLS,
      setSkills: (skills) => set({ skills }),
      toggleSkill: (id: string) =>
        set((s) => ({
          skills: s.skills.map((sk) =>
            sk.id === id ? { ...sk, enabled: !sk.enabled } : sk
          ),
        })),
      ragConfig: { spatialWeight: 60, topK: 5, rerank: true, vectorDb: '', collection: 'geoagent' },
      setRagConfig: (config) => set((s) => ({ ragConfig: { ...s.ragConfig, ...config } })),
      ragSpatial: [],
      setRagSpatial: (docs) => set({ ragSpatial: docs }),
      ragSemantic: [],
      setRagSemantic: (docs) => set({ ragSemantic: docs }),
      mapStyles: DEFAULT_MAP_STYLES,
      setMapStyles: (styles) => set({ mapStyles: styles }),
      llmConfigFull: { baseUrl: 'https://api.openai.com/v1', apiKey: '', model: 'gpt-4o', caching: true },
      setLlmConfigFull: (config) => set((s) => ({ llmConfigFull: { ...s.llmConfigFull, ...config } })),
    }),
    {
      name: 'geoagent-settings',
      partialize: (state) => ({
        mcpServers: state.mcpServers,
        skills: state.skills,
        ragConfig: state.ragConfig,
        mapStyles: state.mapStyles,
        llmConfigFull: state.llmConfigFull,
        baseLayer: state.baseLayer,
      }),
    }
  )
);
