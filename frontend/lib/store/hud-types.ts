import type { Layer } from '@/lib/types/layer';
import type { AnalysisResult, GeoJSONFeatureCollection } from '@/lib/types';

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

export interface HudState {
  /* ─── Layers ─── */
  layers: Layer[];
  addLayer: (layer: Layer) => void;
  removeLayer: (id: string) => void;
  toggleLayer: (id: string) => void;
  updateLayer: (id: string, updates: Partial<Layer>) => void;
  reorderLayers: (layers: Layer[]) => void;
  clearLayers: () => void;

  /* ─── Layer Editing ─── */
  editingLayerId: string | null;
  setEditingLayerId: (id: string | null) => void;

  /* ─── Analysis Navigation ─── */
  analysisResult: AnalysisResult | null;
  setAnalysisResult: (result: AnalysisResult | null) => void;

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
  viewport: { center: [number, number]; zoom: number; bearing: number; pitch: number };
  setViewport: (center: [number, number], zoom: number, bearing?: number, pitch?: number) => void;
  baseLayer: string;
  setBaseLayer: (name: string) => void;
  is3D: boolean;
  setIs3D: (v: boolean) => void;

  /* ─── Perception Buffer (Agent-Everything) ─── */
  _perceptionQueue: Array<{ event: string; data: Record<string, unknown> }>;
  pushPerception: (event: string, data: Record<string, unknown>) => void;
  drainPerception: () => Array<{ event: string; data: Record<string, unknown> }>;

  /* ─── HUD Panel Visibility ─── */
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
  analysisAssets: any[];
  fetchAnalysisAssets: (sessionId?: string) => Promise<void>;
  updateAsset: (assetId: number | string, updates: any) => void;
  deleteAsset: (assetId: number | string) => void;

  /* ─── System Settings ─── */
  settingsOpen: boolean;
  setSettingsOpen: (open: boolean) => void;
  mcpConfig: string;
  setMcpConfig: (config: string) => void;
  llmConfig: any;
  setLlmConfig: (config: any) => void;
  availableSkills: any[];
  setAvailableSkills: (skills: any[]) => void;
}
