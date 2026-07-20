/**
 * UI slice — viewport / 面板可见性 / 主题色 / 操作日志 / 感知队列 / 导出布局 等。
 *
 * 关注点：所有"运行时的临时 UI 状态"。基本不持久化（baseLayer 例外，
 * 因为用户的底图偏好需要重启保留 — 由 useHudStore 的 partialize 决定）。
 */
import type { StateCreator } from 'zustand';
import type { HudState, AiStatus, LeftTab, SettingsTab, ExportItem } from '../hud-types';

export const createUiSlice: StateCreator<HudState, [], [], Partial<HudState>> = (set, get) => ({
  /* ─── HUD Panels (legacy compat) ─── */
  leftPanelOpen: true,
  rightPanelOpen: true,
  toggleLeftPanel: () => set((s) => ({ leftPanelOpen: !s.leftPanelOpen })),
  toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),

  /* ─── Viewport / Base / 3D ─── */
  viewport: { center: [116.4074, 39.9042], zoom: 4, bearing: 0, pitch: 0, bounds: undefined },
  // 审计 F30：之前 setViewport 替换整个 viewport 对象 -- 当调用方只传 4 个参数
  // （不传 bounds）时，bounds 会被设为 undefined，依赖 bounds 的代码（如 chat
  // map_state payload）会读到 undefined。改为 merge，保留未传字段的旧值。
  setViewport: (center, zoom, bearing, pitch, bounds) =>
    set((s) => ({
      viewport: {
        center,
        zoom,
        bearing: bearing ?? s.viewport.bearing ?? 0,
        pitch: pitch ?? s.viewport.pitch ?? 0,
        bounds: bounds ?? s.viewport.bounds,
      },
    })),
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

  /* ─── Perception Buffer（已废弃）───
   * 原本由 use-websocket.ts 推送实时感知事件，但该 hook 从未在任何组件挂载，
   * 整个 perception 通道一直处于"安静失效"状态。useWebSocket hook 在审计
   * PR 4 中被删除，这里同步删除残留 state。如未来恢复实时感知，应改走
   * SSE 单一通道（与 chat 流统一），不再单独维护 WS perception 队列。
   */

  /* ─── System Callback ─── */
  // 审计 F35：之前 pendingSystemMessage 是单字符串，rapid back-to-back 调用
  // 互相覆盖。改为队列：setPendingSystemMessage 入队，consumer 调用
  // clearPendingSystemMessage 取出最早的。
  pendingSystemMessage: null as string | null,
  _systemMessageQueue: [] as string[],
  setPendingSystemMessage: (msg: string | null) =>
    set((s) => {
      if (msg === null) {
        // null = 清空信号（向后兼容）
        return { pendingSystemMessage: null, _systemMessageQueue: [] };
      }
      // 入队；如果当前 pendingSystemMessage 为空则立即激活
      if (s.pendingSystemMessage === null) {
        return { pendingSystemMessage: msg, _systemMessageQueue: [] };
      }
      return { _systemMessageQueue: [...s._systemMessageQueue, msg] };
    }),
  // consumer 调用：取出并激活下一条（无下一条则置 null）
  drainSystemMessage: () =>
    set((s) => {
      if (s._systemMessageQueue.length > 0) {
        const [next, ...rest] = s._systemMessageQueue;
        return { pendingSystemMessage: next, _systemMessageQueue: rest };
      }
      return { pendingSystemMessage: null, _systemMessageQueue: [] };
    }),

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
  fontSize: 15,
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
  addExport: (item: ExportItem) => set((s) => ({ exports: [item, ...s.exports] })),
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
    author: '',
    dataSource: '',
    showWatermark: true,
    showCompass: true,
    showScale: true,
    showLegend: true,
    showMetadata: true,
    showGraticules: false,
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
