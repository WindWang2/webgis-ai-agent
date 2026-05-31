import type { Layer } from '@/lib/types/layer';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import type { ExplorerTask } from '@/lib/types/explorer';

export interface SelectedFeatureInfo {
  layerId: string;          // 渲染层 id (含 custom- 前缀)
  layerName?: string;       // 用户可读名 / 别名
  refId?: string;           // 数据 ref:xxx，方便后端关联 session 数据
  point: [number, number];  // 鼠标点击的 [lng, lat]
  properties: Record<string, unknown>;
  selectedAt: number;       // epoch ms
}

export interface TaskStep {
  id: string;
  tool: string;
  stepIndex: number;
  status: 'pending' | 'running' | 'completed' | 'failed';
  result?: unknown;
  hasGeojson?: boolean;
  error?: string;
  geojsonSnapshot?: GeoJSONFeatureCollection;
  startedAt?: number;
  completedAt?: number;
}

export interface TaskState {
  id: string;
  steps: TaskStep[];
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  stepCount?: number;
  summary?: string;
  planJson?: unknown;
}

export type AiStatus = 'idle' | 'thinking' | 'acting' | 'done' | 'error';
// 新增 v2 类型
export interface OpLogEntry {
  id: string;
  type: 'add' | 'remove' | 'toggle' | 'flyto' | 'style';
  label: string;
  time: string;
  detail?: string;
}

export interface RagResult {
  id: string;
  source: string;
  score: string;
  chunks: number;
  excerpts: string[];
}

export interface ExportItem {
  id: string;
  name: string;
  type: 'png' | 'pdf' | 'geojson';
  size: string;
  date: string;
}

export interface ExportSettings {
  isExportMode: boolean;
  title: string;
  subtitle: string;
  showWatermark: boolean;
  showCompass: boolean;
  showScale: boolean;
  showLegend: boolean;
  paperSize: 'screen' | 'A4';
  orientation: 'landscape' | 'portrait';
  dpi: number;
  format: 'png' | 'pdf' | 'svg' | 'geojson';
}

export interface CausalEntry {
  id: string;
  tool: string;
  mapAction?: string;
  time: string;
  toolInput?: string;
  mapEffect?: string;
  mapState?: Record<string, unknown>;
}

export type LeftTab = 'chat' | 'layers' | 'analysis' | 'ops' | 'exports' | 'assets' | 'export_layout';
export type SettingsTab = 'llm' | 'skills' | 'rag' | 'layers' | 'map' | 'system';

export interface SkillEntry {
  id: string;
  name: string;
  desc: string;
  enabled: boolean;
  calls: number;
  category: string;
}

export interface RagSpatialDoc {
  id: string;
  name: string;
  type: string;
  features: number | null;
  indexed: boolean;
  size: string;
}

export interface RagSemanticDoc {
  id: string;
  name: string;
  chunks: number;
  indexed: boolean;
  size: string;
}

export interface RagConfig {
  spatialWeight: number;
  topK: number;
  rerank: boolean;
  vectorDb: string;
  collection: string;
}

export interface MapStyleEntry {
  id: number;
  name: string;
  desc: string;
  url?: string;
}

export interface AnalysisAsset {
  id: number | string;
  filename?: string;
  original_name?: string;
  name?: string;
  geometry_type?: string | null;
  type?: string;
  created_at?: string | null;
  uploaded_at?: string | null;
  file_size?: number | string | null;
  size?: number | string | null;
}

export interface LlmConfig {
  baseUrl: string;
  apiKey: string;
  model: string;
  caching: boolean;
}

export interface SessionSummary {
  id: string;
  title: string;
  time: string;
  msgs: number;
  tags: string[];
}

export interface HudState {
  /* ─── Layers ─── */
  layers: Layer[];
  addLayer: (layer: Layer) => void;
  removeLayer: (id: string) => void;
  toggleLayer: (id: string) => void;
  updateLayer: (id: string, updates: Partial<Layer>) => void;
  reorderLayers: (layers: Layer[]) => void;
  setLayers: (layers: Layer[]) => void;
  clearLayers: () => void;

  /* ─── Annotations ─── */
  annotations: Record<string, unknown>[];
  addAnnotation: (feature: Record<string, unknown>) => void;
  clearAnnotations: () => void;

  /* ─── Layer Editing ─── */
  editingLayerId: string | null;
  setEditingLayerId: (id: string | null) => void;

  /* ─── Task Stack ─── */
  currentTask: TaskState | null;
  taskStart: (taskId: string) => void;
  stepStart: (taskId: string, stepId: string, stepIndex: number, tool: string) => void;
  stepResult: (taskId: string, stepId: string, tool: string, result: unknown, hasGeojson: boolean, snapshot?: GeoJSONFeatureCollection) => void;
  stepError: (taskId: string, stepId: string, error: string) => void;
  taskComplete: (taskId: string, stepCount: number, summary: string) => void;
  taskError: (taskId: string, error: string) => void;
  taskCancelled: (taskId: string) => void;
  clearTask: () => void;

  /* ─── Process Layers (temporary WS layers) ─── */
  processLayers: Record<string, GeoJSONFeatureCollection>;
  addProcessLayer: (stepId: string, geojson: GeoJSONFeatureCollection) => void;
  removeProcessLayer: (stepId: string) => void;
  clearProcessLayers: () => void;

  /* ─── Map View State (Real-time Perception) ─── */
  viewport: { center: [number, number]; zoom: number; bearing: number; pitch: number; bounds?: [number, number, number, number] };
  setViewport: (center: [number, number], zoom: number, bearing?: number, pitch?: number, bounds?: [number, number, number, number]) => void;
  baseLayer: string;
  setBaseLayer: (name: string) => void;
  is3D: boolean;
  setIs3D: (v: boolean) => void;

  /* ─── Map Load State ─── */
  mapLoaded: boolean;
  setMapLoaded: (v: boolean) => void;

  /* ─── Selected Feature (set by map click, consumed by chat mapState payload) ─── */
  selectedFeature: SelectedFeatureInfo | null;
  setSelectedFeature: (f: SelectedFeatureInfo | null) => void;

  /* ─── Perception Buffer (Agent-Everything) ─── */
  _perceptionQueue: Array<{ event: string; data: Record<string, unknown> }>;
  pushPerception: (event: string, data: Record<string, unknown>) => void;
  drainPerception: () => Array<{ event: string; data: Record<string, unknown> }>;

  /* ─── HUD Panel Visibility (legacy compat during migration) ─── */
  leftPanelOpen: boolean;
  rightPanelOpen: boolean;
  toggleLeftPanel: () => void;
  toggleRightPanel: () => void;

  /* ─── RAG Insight ─── */
  ragInsight: { title: string; content: string; source?: string } | null;
  setRagInsight: (insight: { title: string; content: string; source?: string } | null) => void;

  /* ─── System Callback ─── */
  pendingSystemMessage: string | null;
  setPendingSystemMessage: (msg: string | null) => void;

  /* ─── Analysis Assets ─── */
  analysisAssets: AnalysisAsset[];
  fetchAnalysisAssets: (sessionId?: string) => Promise<void>;
  updateAsset: (assetId: number | string, updates: Partial<AnalysisAsset>) => void;
  deleteAsset: (assetId: number | string) => void;

  /* ─── System Settings ─── */
  settingsOpen: boolean;
  setSettingsOpen: (open: boolean) => void;
  llmConfig: Record<string, unknown>;
  setLlmConfig: (config: Record<string, unknown>) => void;
  availableSkills: SkillEntry[];
  setAvailableSkills: (skills: SkillEntry[]) => void;

  /* ─── Agent UI State ─── */
  aiStatus: AiStatus;
  setAiStatus: (status: AiStatus) => void;
  activeLeftTab: LeftTab;
  setActiveLeftTab: (tab: LeftTab) => void;
  historyOpen: boolean;
  setHistoryOpen: (open: boolean) => void;
  settingsTab: SettingsTab;
  setSettingsTab: (tab: SettingsTab) => void;
  sessions: SessionSummary[];
  setSessions: (sessions: SessionSummary[]) => void;

  /* ─── v2 Panel Visibility ─── */
  hudOpen: boolean;
  setHudOpen: (open: boolean) => void;
  ragPanelOpen: boolean;
  setRagPanelOpen: (open: boolean) => void;
  tweaksOpen: boolean;
  setTweaksOpen: (open: boolean) => void;

  /* ─── v2 UI Tweaks ─── */
  accentColor: string;
  setAccentColor: (color: string) => void;
  theme: 'light' | 'dark';
  setTheme: (theme: 'light' | 'dark') => void;
  fontSize: number;
  setFontSize: (size: number) => void;
  density: 'compact' | 'comfortable';
  setDensity: (density: 'compact' | 'comfortable') => void;
  showGrid: boolean;
  setShowGrid: (show: boolean) => void;
  sidebarWidth: number;
  setSidebarWidth: (width: number) => void;

  /* ─── v2 Feature Data ─── */
  opsLog: OpLogEntry[];
  pushOpLog: (entry: OpLogEntry) => void;
  clearOpsLog: () => void;
  ragResults: RagResult[];
  setRagResults: (results: RagResult[]) => void;
  exports: ExportItem[];
  setExports: (items: ExportItem[]) => void;
  causalChain: CausalEntry[];
  pushCausalEntry: (entry: CausalEntry) => void;
  clearCausalChain: () => void;

  /* ─── Demo Mode ─── */
  demoMode: boolean;
  setDemoMode: (enabled: boolean) => void;

  /* ─── Settings Data ─── */
  skills: SkillEntry[];
  setSkills: (skills: SkillEntry[]) => void;
  toggleSkill: (id: string) => void;
  ragConfig: RagConfig;
  setRagConfig: (config: Partial<RagConfig>) => void;
  ragSpatial: RagSpatialDoc[];
  setRagSpatial: (docs: RagSpatialDoc[]) => void;
  ragSemantic: RagSemanticDoc[];
  setRagSemantic: (docs: RagSemanticDoc[]) => void;
  mapStyles: MapStyleEntry[];
  setMapStyles: (styles: MapStyleEntry[]) => void;
  llmConfigFull: LlmConfig;
  setLlmConfigFull: (config: Partial<LlmConfig>) => void;

  /* ─── Cartography Live Context ─── */
  cartographyTitle: string | null;
  setCartographyTitle: (title: string | null) => void;
  focusLayerId: string | null;
  focusLayer: (layerId: string | null) => void;

  /* --- Explorer Tasks --- */
  explorerTasks: ExplorerTask[];
  addExplorerTask: (task: ExplorerTask) => void;
  updateExplorerTask: (taskId: string, updates: Partial<ExplorerTask>) => void;
  removeExplorerTask: (taskId: string) => void;

  /* ─── Export Layout ─── */
  exportSettings: ExportSettings;
  updateExportSettings: (updates: Partial<ExportSettings>) => void;
}

export interface ToolCallEntry {
  id: string;
  tool: string;
  arguments?: string;
  status: 'running' | 'completed' | 'failed';
  result?: any;
  hasGeojson?: boolean;
  error?: string;
  startedAt?: number;
  completedAt?: number;
}

export interface PlanProposalPayload {
  plan_id: string;
  title: string;
  summary?: string;
  step_count: number;
  destructive_steps?: string[];
  steps_preview?: Array<{ id: string; tool: string; purpose?: string; destructive?: boolean }>;
  status: 'pending' | 'approved' | 'rejected';
}
