'use client';
/**
 * Dock slice — workspace panel dock model (Workspace V2 / Goal C5).
 *
 * 轻量 dock 基座（刻意不是 IDE docking framework）：right / bottom 两个
 * 停靠区 + 浮动（float = 不停靠，组件留在地图 chrome 上的 FloatingChrome
 * 定位体系里）。
 *
 * 边界（ADR 待文档化，与本实现一致）：
 * - dock placement 是**工作区 UI 状态**，与语义组件状态（MapSpec
 *   placement / enabled / collapsed —— 唯一组件真相）严格分离：
 *   「图表数据」与「图表停靠位置」不是同一个对象；
 * - 不持久化（会话级工作区布局；persist partialize 不含本 slice）；
 * - 不进 MapSpec / LLM context。
 */
import type { StateCreator } from 'zustand';
import type { HudState } from '../hud-types';

/** 停靠区词表（封闭）：float = 不停靠（地图 chrome 浮动）。 */
export type DockRegion = 'float' | 'right' | 'bottom';

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
  /** 停靠面板到指定区（float = 取消停靠，面板回到地图 chrome）。 */
  dockPanel: (panelId: string, region: DockRegion) => void;
  toggleRightDock: () => void;
  toggleBottomDock: () => void;
  /** 多面板标签切换。 */
  setActiveDockPanel: (region: 'right' | 'bottom', panelId: string) => void;
  /** 会话切换清理（面板实例随 MapSpec 生命周期走，dock 状态不跨会话）。 */
  resetDockState: () => void;
}

const EMPTY_REGION: DockRegionState = { open: false, panels: [], activePanel: null };

export const createDockSlice: StateCreator<HudState, [], [], DockSlice> = (set, get) => ({
  dockPlacements: {},
  rightDock: { ...EMPTY_REGION },
  bottomDock: { ...EMPTY_REGION },

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

  toggleRightDock: () =>
    set((s) => ({ rightDock: { ...s.rightDock, open: !s.rightDock.open } })),

  toggleBottomDock: () =>
    set((s) => ({ bottomDock: { ...s.bottomDock, open: !s.bottomDock.open } })),

  setActiveDockPanel: (region, panelId) =>
    set((s) => {
      const state = region === 'right' ? s.rightDock : s.bottomDock;
      if (!state.panels.includes(panelId)) return s;
      const next = { ...state, activePanel: panelId };
      return region === 'right' ? { rightDock: next } : { bottomDock: next };
    }),

  resetDockState: () =>
    set({
      dockPlacements: {},
      rightDock: { ...EMPTY_REGION },
      bottomDock: { ...EMPTY_REGION },
    }),
});
