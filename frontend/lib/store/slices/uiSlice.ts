/**
 * UI slice — viewport / 面板可见性 / 主题色 / 操作日志 / 感知队列 / 导出布局 等。
 *
 * 关注点：所有"运行时的临时 UI 状态"。基本不持久化（baseLayer 例外，
 * 因为用户的底图偏好需要重启保留 — 由 useHudStore 的 partialize 决定）。
 */
import type { StateCreator } from 'zustand';
import type { HudState, AiStatus, LeftTab, SettingsTab } from '../hud-types';

export const createUiSlice: StateCreator<HudState, [], [], Partial<HudState>> = (set, get) => ({
  /* ─── HUD Panels (legacy compat) ─── */
  leftPanelOpen: true,
  rightPanelOpen: true,
  toggleLeftPanel: () => set((s) => ({ leftPanelOpen: !s.leftPanelOpen })),
  toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),

  /* ─── Viewport / Base / 3D ─── */
  viewport: { center: [116.4074, 39.9042], zoom: 4, bearing: 0, pitch: 0, bounds: undefined },
  setViewport: (center, zoom, bearing, pitch, bounds) =>
    set({ viewport: { center, zoom, bearing: bearing ?? 0, pitch: pitch ?? 0, bounds } }),
  baseLayer: 'Carto 深色',
  setBaseLayer: (name) => set({ baseLayer: name }),
  is3D: false,
  setIs3D: (v: boolean) => set({ is3D: v }),

  /* ─── Map Load State ─── */
  mapLoaded: false,
  setMapLoaded: (v: boolean) => set({ mapLoaded: v }),

  /* ─── Selected Feature ─── */
  selectedFeature: null,
  setSelectedFeature: (f) => set({ selectedFeature: f }),

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

  /* ─── Agent UI ─── */
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

  /* ─── v2 Panel Visibility ─── */
  hudOpen: false,
  setHudOpen: (open) => set({ hudOpen: open }),
  ragPanelOpen: false,
  setRagPanelOpen: (open) => set({ ragPanelOpen: open }),
  tweaksOpen: false,
  setTweaksOpen: (open) => set({ tweaksOpen: open }),

  /* ─── v2 UI Tweaks ─── */
  accentColor: '#16a34a',
  setAccentColor: (color) => set({ accentColor: color }),
  theme: 'light' as const,
  setTheme: (theme) => set({ theme }),
  fontSize: 13,
  setFontSize: (size) => set({ fontSize: size }),
  density: 'compact' as const,
  setDensity: (density) => set({ density }),
  showGrid: true,
  setShowGrid: (show) => set({ showGrid: show }),
  sidebarWidth: 330,
  setSidebarWidth: (width) => set({ sidebarWidth: width }),

  /* ─── v2 Feature Data ─── */
  opsLog: [],
  pushOpLog: (entry) => set((s) => ({ opsLog: [entry, ...s.opsLog] })),
  clearOpsLog: () => set({ opsLog: [] }),
  ragResults: [],
  setRagResults: (results) => set({ ragResults: results }),
  exports: [],
  setExports: (items) => set({ exports: items }),
  causalChain: [],
  pushCausalEntry: (entry) => set((s) => ({ causalChain: [entry, ...s.causalChain] })),
  clearCausalChain: () => set({ causalChain: [] }),

  /* ─── Demo Mode ─── */
  demoMode: false,
  setDemoMode: (enabled) => set({ demoMode: enabled }),

  /* ─── Cartography Live Context ─── */
  cartographyTitle: null,
  setCartographyTitle: (title) => set({ cartographyTitle: title }),
  focusLayerId: null,
  focusLayer: (layerId) => set({ focusLayerId: layerId }),

  /* ─── Export Layout ─── */
  exportSettings: {
    isExportMode: false,
    title: '',
    subtitle: '',
    showWatermark: true,
    showCompass: true,
    showScale: true,
    showLegend: true,
    paperSize: 'screen',
    orientation: 'landscape',
    dpi: 96,
    format: 'png',
  },
  updateExportSettings: (updates) =>
    set((s) => ({
      exportSettings: { ...s.exportSettings, ...updates },
    })),
});
