'use client';

import { create } from 'zustand';
import type { Layer } from '@/lib/types/layer';
import type { AnalysisResult, GeoJSONFeatureCollection } from '@/lib/types';
import { API_BASE } from '../api/config';

/* ══════════════════════════════════════════
   Task Types
   ══════════════════════════════════════════ */

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

/* ══════════════════════════════════════════
   HUD Store — Unified Global State
   ══════════════════════════════════════════ */

interface HudState {
  /* ─── Layers ─── */
  layers: Layer[];
  addLayer: (layer: Layer) => void;
  removeLayer: (id: string) => void;
  toggleLayer: (id: string) => void;
  updateLayer: (id: string, updates: Partial<Layer>) => void;
  reorderLayers: (layers: Layer[]) => void;
  clearLayers: () => void;

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
  viewport: { center: [number, number]; zoom: number };
  setViewport: (center: [number, number], zoom: number) => void;

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
  updateAsset: (assetId: number, updates: any) => void;
  deleteAsset: (assetId: number) => void;
  
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

export const useHudStore = create<HudState>((set) => ({
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

  /* ─── HUD Panels ─── */
  leftPanelOpen: true,
  rightPanelOpen: true,
  toggleLeftPanel: () => set((s) => ({ leftPanelOpen: !s.leftPanelOpen })),
  toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),

  /* ─── RAG ─── */
  ragInsight: null,
  setRagInsight: (insight) => set({ ragInsight: insight }),

  /* ─── Viewport Sync ─── */
  viewport: { center: [116.4074, 39.9042], zoom: 4 },
  setViewport: (center, zoom) => set({ viewport: { center, zoom } }),

  /* ─── System Callback ─── */
  pendingSystemMessage: null,
  setPendingSystemMessage: (msg) => set({ pendingSystemMessage: msg }),

  /* ─── Analysis Assets ─── */
  analysisAssets: [],
  fetchAnalysisAssets: async (sessionId) => {
    try {
      const url = `${API_BASE}/api/v1/chat/tools/call?tool=list_analysis_assets&session_id=${sessionId || ''}`;
      // In a real app we'd have a specific GET route, but our current tools can be invoked via specific API if set up, 
      // or we just call the helper defined in nature_resources. 
      // For now, let's assume a direct GET endpoint for assets if we want to be clean, 
      // but I'll implement a fetch from the list_analysis_assets tool logic.
      const resp = await fetch(`${API_BASE}/api/v1/uploads?session_id=${sessionId || ''}`);
      if (resp.ok) {
        const data = await resp.json();
        // Filter for raster_analysis types
        const assets = data.uploads.filter((u: any) => u.geometry_type === "raster_analysis");
        set({ analysisAssets: assets });
      }
    } catch (e) {
      console.error("Failed to fetch assets:", e);
    }
  },
  updateAsset: (id, updates) => set(s => ({
    analysisAssets: s.analysisAssets.map(a => a.id === id ? { ...a, ...updates } : a)
  })),
  deleteAsset: (id) => set(s => ({
    analysisAssets: s.analysisAssets.filter(a => a.id !== id)
  })),

  /* ─── Settings ─── */
  settingsOpen: false,
  setSettingsOpen: (open) => set({ settingsOpen: open }),
  mcpConfig: "{}",
  setMcpConfig: (config) => set({ mcpConfig: config }),
  llmConfig: {},
  setLlmConfig: (config) => set({ llmConfig: config }),
  availableSkills: [],
  setAvailableSkills: (skills) => set({ availableSkills: skills }),
}));
