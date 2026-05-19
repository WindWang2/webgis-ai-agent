/**
 * Layers slice — 矢量/栅格图层、编辑选中、过程层、分析资产。
 *
 * 关注点：地图上的可视/可操作要素集合。
 */
import type { StateCreator } from 'zustand';
import { API_BASE } from '../../api/config';
import type { HudState } from '../hud-types';

export const createLayersSlice: StateCreator<HudState, [], [], Partial<HudState>> = (set, get) => ({
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
  setLayers: (layers) => set({ layers }),
  clearLayers: () => set({ layers: [] }),

  /* ─── Layer Editing ─── */
  editingLayerId: null,
  setEditingLayerId: (id) => set({ editingLayerId: id }),

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

  /* ─── Analysis Assets (后端遥感产物) ─── */
  analysisAssets: [],
  fetchAnalysisAssets: async (sessionId: string | undefined) => {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/uploads?session_id=${sessionId || ''}`);
      if (resp.ok) {
        const data = await resp.json();
        const assets = data.uploads.filter((u: { geometry_type?: string }) => u.geometry_type === 'raster_analysis');
        set({ analysisAssets: assets });
      }
    } catch (e) {
      console.error('Failed to fetch assets:', e);
    }
    void get;
  },
  updateAsset: (id, updates) =>
    set((s) => ({
      analysisAssets: s.analysisAssets.map((a) => (a.id === id ? { ...a, ...updates } : a)),
    })),
  deleteAsset: (id) =>
    set((s) => ({
      analysisAssets: s.analysisAssets.filter((a) => a.id !== id),
    })),
});
