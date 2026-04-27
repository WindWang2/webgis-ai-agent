'use client';

import { create } from 'zustand';
import { API_BASE } from '../api/config';
import type { HudState } from './hud-types';

export type { HudState, TaskStep, TaskState } from './hud-types';

export const useHudStore = create<HudState>((set, get) => ({
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

  /* ─── HUD Panels ─── */
  leftPanelOpen: true,
  rightPanelOpen: true,
  toggleLeftPanel: () => set((s) => ({ leftPanelOpen: !s.leftPanelOpen })),
  toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),

  /* ─── RAG ─── */
  ragInsight: null,
  setRagInsight: (insight) => set({ ragInsight: insight }),

  /* ─── Viewport Sync ─── */
  viewport: { center: [116.4074, 39.9042], zoom: 4, bearing: 0, pitch: 0 },
  setViewport: (center, zoom, bearing, pitch) =>
    set({ viewport: { center, zoom, bearing: bearing ?? 0, pitch: pitch ?? 0 } }),
  baseLayer: 'Carto Dark',
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

  /* ─── Settings ─── */
  settingsOpen: false,
  setSettingsOpen: (open: boolean) => set({ settingsOpen: open }),
  mcpConfig: "{}",
  setMcpConfig: (config: string) => set({ mcpConfig: config }),
  llmConfig: {},
  setLlmConfig: (config: Record<string, unknown>) => set({ llmConfig: config }),
  availableSkills: [],
  setAvailableSkills: (skills: any[]) => set({ availableSkills: skills }),
}));
