/**
 * Layers slice — 矢量/栅格图层、编辑选中、过程层、分析资产。
 *
 * 关注点：地图上的可视/可操作要素集合。
 */
import type { StateCreator } from 'zustand';
import { listUploads } from '../../api/upload';
import { isApiError } from '../../api/transport';
import type { HudState } from '../hud-types';


import { devOnly } from "@/lib/utils/logger";

// F-2: last-writer-wins guard for fetchAnalysisAssets. A slow response for a
// previous session must not overwrite the current session's asset list after a
// session switch (the action takes no AbortSignal, so this is the correctness
// guard). Only the most recent call's resolved value is applied.
let _analysisAssetsSeq = 0;

/**
 * FE-3: 注释（annotation）队列上限。AI 长会话中反复标注会无限增长
 * （findings E4 无界数组），超限丢弃最旧条目。
 */
export const MAX_ANNOTATIONS = 500;

const PRESENTATION_FIELDS = new Set([
  'visible', 'opacity', 'style', 'legend_spec', 'type', '_refId', '_mapspecLayerId',
]);

/**
 * Server-authored cartographic tags certify one exact presentation generation.
 * A user-facing presentation change must invalidate those tags; source-body
 * hydration and explicitly tagged server updates may retain them.
 */
function withAttestationPolicy(
  updates: Partial<HudState['layers'][number]>,
): Partial<HudState['layers'][number]> {
  const serverAttested = typeof updates._mapspecFingerprint === 'string';
  const changesPresentation = Object.keys(updates).some((key) => PRESENTATION_FIELDS.has(key));
  if (serverAttested || !changesPresentation) return updates;
  return {
    ...updates,
    _mapspecFingerprint: undefined,
    _mapspecProjectionFingerprint: undefined,
    _mapspecRepairActionId: undefined,
  };
}

export const createLayersSlice: StateCreator<HudState, [], [], Partial<HudState>> = (set, get) => ({
  /* ─── Layers ─── */
  layers: [],
  layerIntentGeneration: 0,
  addLayer: (layer) => set((s) => {
    if (s.layers.some(l => l.id === layer.id)) return s;
    const generation = s.layerIntentGeneration + 1;
    return {
      layerIntentGeneration: generation,
      layers: [{ ...layer, _intentGeneration: generation }, ...s.layers],
    };
  }),
  removeLayer: (id) => set((s) => ({
    layerIntentGeneration: s.layerIntentGeneration + 1,
    layers: s.layers.filter((l) => l.id !== id),
  })),
  toggleLayer: (id) =>
    set((s) => {
      const generation = s.layerIntentGeneration + 1;
      return {
        layerIntentGeneration: generation,
        layers: s.layers.map((l) => (
          l.id === id
            ? {
                ...l,
                visible: !l.visible,
                _mapspecFingerprint: undefined,
                _mapspecProjectionFingerprint: undefined,
                _mapspecRepairActionId: undefined,
                _intentGeneration: generation,
              }
            : l
        )),
      };
    }),
  updateLayer: (id, updates) =>
    set((s) => {
      const generation = s.layerIntentGeneration + 1;
      return {
        layerIntentGeneration: generation,
        layers: s.layers.map((l) => (
          l.id === id
            ? { ...l, ...withAttestationPolicy(updates), _intentGeneration: generation }
            : l
        )),
      };
    }),
  reorderLayers: (layers) => set({ layers }),
  setLayers: (layers) => set((s) => {
    const generation = s.layerIntentGeneration + 1;
    return {
      layerIntentGeneration: generation,
      layers: layers.map((layer) => ({ ...layer, _intentGeneration: generation })),
    };
  }),
  clearLayers: () => set((s) => ({
    layerIntentGeneration: s.layerIntentGeneration + 1,
    layers: [],
  })),

  /* ─── Annotations ─── */
  annotations: [],
  // FE-3: 上限 500 —— 长会话 AI 反复 add_marker / draw_measurement 时防止
  // 无限增长（findings E4）。超限时丢最旧的（append 队尾保留最新）。
  addAnnotation: (feature) => set((s) => {
    const next = [...s.annotations, feature];
    return { annotations: next.length > MAX_ANNOTATIONS ? next.slice(next.length - MAX_ANNOTATIONS) : next };
  }),
  clearAnnotations: () => set({ annotations: [] }),

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
    // F-2: capture a per-call sequence so a stale response (e.g. for a previous
    // session after a switch) cannot overwrite the current session's assets.
    const seq = ++_analysisAssetsSeq;
    try {
      // listUploads goes through the Fast Path — parallel mount callers and
      // session-switch follow-up fetch share one roundtrip. errors flow as
      // typed ApiError so we can distinguish abort/timeout/HTTP.
      const data = await listUploads(sessionId);
      if (seq !== _analysisAssetsSeq) return; // superseded by a newer fetch
      const assets = (data.uploads || []).filter(
        (u) => u.geometry_type === 'raster_analysis'
      );
      set({ analysisAssets: assets });
    } catch (e) {
      if (!isApiError(e)) devOnly.error('Failed to fetch assets:', e);
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
