'use client';
/**
 * Dock slice — workspace panel dock model (Workspace V2 / Goal C5 → V7 布局系统)。
 *
 * 轻量 dock 基座（刻意不是 IDE docking framework）：right / bottom 两个
 * 停靠区 + 浮动（float = 不停靠，组件留在地图 chrome 上的 FloatingChrome
 * 定位体系里）。
 *
 * 边界（ADR 待文档化，与本实现一致）：
 * - dock placement 是**工作区 UI 状态**，与语义组件状态（MapSpec
 *   placement / enabled / collapsed —— 唯一组件真相）严格分离：
 *   「图表数据」与「图表停靠位置」不是同一个对象；
 * - V7 布局系统：区尺寸（宽度/高度）进本 slice 并持久化（partialize
 *   白名单，刷新恢复）；折叠只隐藏区（成员与归属保留，布局可恢复）；
 * - 不进 MapSpec / LLM context。
 */
import type { StateCreator } from 'zustand';
import type { HudState } from '../hud-types';

/** 停靠区词表（封闭）：float = 不停靠（地图 chrome 浮动）。 */
export type DockRegion = 'float' | 'right' | 'bottom';

export type DockArea = 'right' | 'bottom';

/**
 * V7：静态停靠面板 —— 非宿主为 MapSpec component 的内置工作台面板。
 * 它们不随 spec 演进 prune（pruneDockPanels 的 valid 集恒含这些 id），
 * 但仍是 dock 状态（dockPanel/dockPlacements 通吃）。
 */
export const STATIC_DOCK_PANELS: ReadonlySet<string> = new Set(['attribute-table', 'agent-run']);

export interface DockRegionState {
  open: boolean;
  /** 该区停靠的面板 id（dock 声明序）。 */
  panels: string[];
  /** 多面板时的活动标签（单面板时等于唯一成员）。 */
  activePanel: string | null;
}

export interface DockSlice {
  /** 面板 id（= 组件实例 id）→ 停靠区。缺省 float（不停靠）。 */
  dockPlacements: Record<string, DockRegion>;
  rightDock: DockRegionState;
  bottomDock: DockRegionState;
  /** V7：区尺寸（可拖拽调整并持久化；resetDockSizes 复位默认值）。 */
  rightDockWidth: number;
  bottomDockHeight: number;
  setRightDockWidth: (width: number) => void;
  setBottomDockHeight: (height: number) => void;
  resetDockSizes: () => void;
  /** 停靠面板到指定区（float = 取消停靠，面板回到地图 chrome）。 */
  dockPanel: (panelId: string, region: DockRegion) => void;
  /**
   * V7：折叠/展开区。折叠只翻 open —— 成员与归属保留（重新展开即恢复
   * 原布局）；「面板回浮动」是显式的 undockRegion（旧 toggle 语义）。
   */
  toggleDock: (area: DockArea) => void;
  /** 整区解除停靠：全部成员回浮动（面板留在地图 chrome 定位体系）。 */
  undockRegion: (area: DockArea) => void;
  /** 多面板标签切换。 */
  setActiveDockPanel: (area: DockArea, panelId: string) => void;
  /** V7：属性表面板当前绑定的图层（null = 自动跟随图层选择/首层）。 */
  attributeTableLayerId: string | null;
  setAttributeTableLayerId: (layerId: string | null) => void;
  /** 会话切换清理（面板实例随 MapSpec 生命周期走，dock 状态不跨会话）。 */
  resetDockState: () => void;
  /** V7：一键复位工作台布局（dock 状态/尺寸 + 左栏宽度/开合）。 */
  resetWorkbenchLayout: () => void;
  /** spec 演进清理：组件实例离开 MapSpec 时，其 dock 归属随之失效。 */
  pruneDockPanels: (validPanelIds: ReadonlySet<string>) => void;
}

export const RIGHT_DOCK_DEFAULT_WIDTH = 340;
export const BOTTOM_DOCK_DEFAULT_HEIGHT = 300;
export const RIGHT_DOCK_MIN_WIDTH = 260;
export const RIGHT_DOCK_MAX_WIDTH = 560;
export const BOTTOM_DOCK_MIN_HEIGHT = 160;
export const BOTTOM_DOCK_MAX_HEIGHT = 560;

const EMPTY_REGION: DockRegionState = { open: false, panels: [], activePanel: null };

const clamp = (v: number, min: number, max: number) =>
  Math.min(max, Math.max(min, Math.round(v)));

export const createDockSlice: StateCreator<HudState, [], [], DockSlice> = (set, get) => ({
  dockPlacements: {},
  rightDock: { ...EMPTY_REGION },
  bottomDock: { ...EMPTY_REGION },
  rightDockWidth: RIGHT_DOCK_DEFAULT_WIDTH,
  bottomDockHeight: BOTTOM_DOCK_DEFAULT_HEIGHT,

  setRightDockWidth: (width) =>
    set({ rightDockWidth: clamp(width, RIGHT_DOCK_MIN_WIDTH, RIGHT_DOCK_MAX_WIDTH) }),
  setBottomDockHeight: (height) =>
    set({ bottomDockHeight: clamp(height, BOTTOM_DOCK_MIN_HEIGHT, BOTTOM_DOCK_MAX_HEIGHT) }),
  resetDockSizes: () =>
    set({ rightDockWidth: RIGHT_DOCK_DEFAULT_WIDTH, bottomDockHeight: BOTTOM_DOCK_DEFAULT_HEIGHT }),

  dockPanel: (panelId, region) => {
    if (!panelId) return;
    const { dockPlacements, rightDock, bottomDock } = get();
    const current = dockPlacements[panelId] ?? 'float';
    if (current === region) return;

    const removeFrom = (state: DockRegionState): DockRegionState => {
      const panels = state.panels.filter((id) => id !== panelId);
      return {
        ...state,
        panels,
        activePanel:
          state.activePanel === panelId ? (panels[panels.length - 1] ?? null) : state.activePanel,
      };
    };
    const addTo = (state: DockRegionState): DockRegionState => ({
      // Docking a panel opens its region (dock-invisible regions are noise).
      open: true,
      panels: [...state.panels, panelId],
      activePanel: panelId,
    });

    let right = rightDock;
    let bottom = bottomDock;
    if (current === 'right') right = removeFrom(right);
    if (current === 'bottom') bottom = removeFrom(bottom);
    if (region === 'right') right = addTo(right);
    if (region === 'bottom') bottom = addTo(bottom);

    const placements = { ...dockPlacements };
    if (region === 'float') delete placements[panelId];
    else placements[panelId] = region;

    set({ dockPlacements: placements, rightDock: right, bottomDock: bottom });
  },

  // V7：折叠 = 隐藏该区（成员与归属保留，再展开即恢复布局）。此前
  // 「toggle = 全员回浮动」把收起与解除停靠混为一谈：重新打开需要逐个
  // 再停靠，布局不可恢复（审计 §2-M）。浮回地图 chrome 是显式 undockRegion。
  toggleDock: (area) =>
    set((s) =>
      area === 'right'
        ? { rightDock: { ...s.rightDock, open: !s.rightDock.open } }
        : { bottomDock: { ...s.bottomDock, open: !s.bottomDock.open } }
    ),

  undockRegion: (area) => {
    const ids = area === 'right' ? get().rightDock.panels : get().bottomDock.panels;
    for (const id of ids) get().dockPanel(id, 'float');
  },

  setActiveDockPanel: (area, panelId) =>
    set((s) => {
      const state = area === 'right' ? s.rightDock : s.bottomDock;
      if (!state.panels.includes(panelId)) return s;
      const next = { ...state, activePanel: panelId };
      return area === 'right' ? { rightDock: next } : { bottomDock: next };
    }),

  attributeTableLayerId: null,
  setAttributeTableLayerId: (layerId) => set({ attributeTableLayerId: layerId }),

  resetDockState: () =>
    set({
      dockPlacements: {},
      rightDock: { ...EMPTY_REGION },
      bottomDock: { ...EMPTY_REGION },
      attributeTableLayerId: null,
    }),

  resetWorkbenchLayout: () => {
    get().resetDockState();
    get().resetDockSizes();
    // 左栏复位到默认组合（宽度 330 + 展开）；tab/模式是语义上下文，
    // 不属于「布局」范畴，保留不动。
    set({ sidebarWidth: 330, leftPanelOpen: true });
  },

  pruneDockPanels: (validPanelIds) => {
    const { dockPlacements, rightDock, bottomDock } = get();
    const stale = Object.entries(dockPlacements)
      .filter(([id]) => !validPanelIds.has(id))
      .map(([id]) => id);
    if (!stale.length) return;
    const prune = (state: DockRegionState): DockRegionState => {
      const panels = state.panels.filter((id) => validPanelIds.has(id));
      return {
        ...state,
        panels,
        open: panels.length > 0 ? state.open : false,
        activePanel:
          state.activePanel && panels.includes(state.activePanel)
            ? state.activePanel
            : (panels[panels.length - 1] ?? null),
      };
    };
    const placements = { ...dockPlacements };
    for (const id of stale) delete placements[id];
    set({
      dockPlacements: placements,
      rightDock: prune(rightDock),
      bottomDock: prune(bottomDock),
    });
  },
});
