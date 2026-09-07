'use client';
import type { StateCreator } from 'zustand';
import type { HudState } from '../hud-types';

/**
 * Workbench slice — Workbench V4 统一工作区投影（Goal C / Wave 1）。
 *
 * 设计边界（ADR-0104）：
 * - 本 slice 是 **UI projection / transient state**，绝不承载地图语义真相：
 *   权威地图内容仍以 MapSpec / backend contract 为准（session-cursor 镜像 +
 *   user-mutation CAS 串行链），图层语义分组仍在 `Layer.group`。这里只放
 *   「工作台怎么摆、当前在操作什么」这类会话级 UI 状态。
 * - 单一 store：本 slice 并入 useHudStore（不建第二 store、不建第二 truth）。
 * - 不持久化（partialize 不含本 slice）：分组/选择/模式都是会话工作台状态，
 *   持久化会引入 stale layer id 垃圾与跨会话误恢复。
 *
 * 子域：
 * - mode：Explore / Analyze / Compose 三模式（Wave 3）。模式只改变面板组合
 *   / 工具栏 / 上下文控件，不复制地图状态；每个模式记忆自己的 active tab。
 * - layer selection：多选（批量操作 / Wave 2 Layer Workspace 的选择真相）。
 * - layer groups：用户分组树（UI projection —— 语义组 `Layer.group` 之上的
 *   可折叠/可命名组织结构，不进 MapSpec）。
 * - artifact selection：当前关注的产物（results/chart）。
 * - comparison：对比工作区状态（Wave 8；两视图同步策略可配置）。
 */

/** 工作台模式词表（封闭）。 */
export type WorkbenchMode = 'explore' | 'analyze' | 'compose';

export const WORKBENCH_MODES: readonly WorkbenchMode[] = ['explore', 'analyze', 'compose'];

/** 模式 → 左栏 tab 组合（封闭词表投影；chat 恒在场）。 */
export const MODE_TABS: Record<WorkbenchMode, readonly string[]> = {
  explore: ['chat', 'layers', 'data_sources', 'project', 'tasks'],
  analyze: ['chat', 'analysis', 'results', 'layers', 'tasks'],
  compose: ['chat', 'components', 'export_layout', 'exports', 'layers'],
};

/** 模式切换来源（Agent 不应静默切换 —— 需要可发现的回执）。 */
export type WorkbenchModeOrigin = 'user' | 'agent';

export interface LayerGroupEntity {
  id: string;
  name: string;
  collapsed: boolean;
}

/** 对比视图词表（封闭）。 */
export type ComparisonKind = 'side-by-side' | 'swipe';

export interface ComparisonState {
  active: boolean;
  kind: ComparisonKind;
  /** 左/主视图图层（baseline）。 */
  primaryLayerId: string | null;
  /** 右/副视图图层（comparee）。 */
  secondaryLayerId: string | null;
  /** 视图同步策略（可配置；swipe 默认全同步）。 */
  syncPan: boolean;
  syncZoom: boolean;
  /** swipe 分割位置（0..1，视口宽度比例）。 */
  position: number;
}

export interface WorkbenchSlice {
  /* ─── Mode ─── */
  mode: WorkbenchMode;
  /** 每个模式记忆的 active tab（模式切换不丢上下文）。 */
  modeActiveTab: Record<WorkbenchMode, string>;
  /** 最近一次模式切换的来源（agent 切换时 UI 展示可发现回执）。 */
  modeOrigin: WorkbenchModeOrigin | null;
  /** 切换模式。tab 缺省用该模式记忆值（无记忆用组合首个）。 */
  setWorkbenchMode: (mode: WorkbenchMode, origin?: WorkbenchModeOrigin) => void;

  /* ─── Layer Selection（批量操作选择真相）─── */
  selectedLayerIds: string[];
  setSelectedLayerIds: (ids: string[]) => void;
  toggleLayerSelected: (id: string) => void;
  clearLayerSelection: () => void;

  /* ─── Layer Lock（锁定 = 用户意图护栏：UI 禁操作 + agent/turn-focus 豁免）─── */
  lockedLayerIds: string[];
  toggleLayerLocked: (id: string) => void;

  /* ─── Isolate（solo：进入时记录其余层可见性快照，退出恢复）─── */
  /** isolate 激活时的目标层（null = 未隔离）。 */
  isolatedLayerId: string | null;
  /** isolate 前各层可见性快照（退出时恢复；仅会话内有效）。 */
  isolatedFrom: Record<string, boolean> | null;
  /** 进入隔离：layer-ops 传入隔离前可见性快照（仅可见层需要记录）。 */
  beginIsolate: (layerId: string, snapshot: Record<string, boolean>) => void;
  clearIsolate: () => void;

  /* ─── Layer Groups（UI projection 组织树）─── */
  layerGroups: LayerGroupEntity[];
  layerGroupMembership: Record<string, string>;
  createLayerGroup: (name: string) => string;
  renameLayerGroup: (groupId: string, name: string) => void;
  removeLayerGroup: (groupId: string) => void;
  toggleGroupCollapsed: (groupId: string) => void;
  /** 把图层指派到组（groupId = null → 移出组）。 */
  assignLayersToGroup: (layerIds: string[], groupId: string | null) => void;
  /** 会话切换 / 图层删除后的组成员清理（防 stale id 垃圾）。 */
  pruneLayerGroups: (validLayerIds: ReadonlySet<string>) => void;
  resetLayerGroups: () => void;

  /* ─── Artifact Selection ─── */
  selectedArtifactId: string | null;
  setSelectedArtifactId: (id: string | null) => void;

  /* ─── Comparison（Wave 8）─── */
  comparison: ComparisonState;
  enterComparison: (patch?: Partial<Omit<ComparisonState, 'active'>>) => void;
  updateComparison: (patch: Partial<Omit<ComparisonState, 'active'>>) => void;
  exitComparison: () => void;
}

const EMPTY_COMPARISON: ComparisonState = {
  active: false,
  kind: 'swipe',
  primaryLayerId: null,
  secondaryLayerId: null,
  syncPan: true,
  syncZoom: true,
  position: 0.5,
};

let groupSeq = 0;

export const createWorkbenchSlice: StateCreator<HudState, [], [], Partial<HudState>> = (set, get) => {
  void get;
  return {
    /* ─── Mode ─── */
    mode: 'explore',
    modeActiveTab: { explore: 'chat', analyze: 'analysis', compose: 'components' },
    modeOrigin: null,
    setWorkbenchMode: (mode, origin = 'user') =>
      set((s) => {
        if (s.mode === mode) {
          return s.modeOrigin === origin ? s : { modeOrigin: origin };
        }
        return { mode, modeOrigin: origin };
      }),

    /* ─── Layer Selection ─── */
    selectedLayerIds: [],
    setSelectedLayerIds: (ids) =>
      set({ selectedLayerIds: Array.from(new Set(ids)) }),
    toggleLayerSelected: (id) =>
      set((s) => {
        if (s.selectedLayerIds.includes(id)) {
          return { selectedLayerIds: s.selectedLayerIds.filter((x) => x !== id) };
        }
        return { selectedLayerIds: [...s.selectedLayerIds, id] };
      }),
    clearLayerSelection: () => set({ selectedLayerIds: [] }),

    /* ─── Layer Lock ─── */
    lockedLayerIds: [],
    toggleLayerLocked: (id) =>
      set((s) => {
        if (s.lockedLayerIds.includes(id)) {
          return { lockedLayerIds: s.lockedLayerIds.filter((x) => x !== id) };
        }
        return { lockedLayerIds: [...s.lockedLayerIds, id] };
      }),

    /* ─── Isolate ─── */
    isolatedLayerId: null,
    isolatedFrom: null,
    // isolate 本体只记快照与目标；可见性批量落库由 lib/layers/layer-ops 的
    // isolateLayerAndCommit 执行（保持「UI 状态」与「地图 mutation」分层）。
    beginIsolate: (layerId, snapshot) =>
      set({ isolatedLayerId: layerId, isolatedFrom: { ...snapshot } }),
    clearIsolate: () =>
      set({ isolatedLayerId: null, isolatedFrom: null }),

    /* ─── Layer Groups ─── */
    layerGroups: [],
    layerGroupMembership: {},
    createLayerGroup: (name) => {
      groupSeq += 1;
      const id = `wg-${groupSeq}`;
      set((s) => ({ layerGroups: [...s.layerGroups, { id, name, collapsed: false }] }));
      return id;
    },
    renameLayerGroup: (groupId, name) =>
      set((s) => ({
        layerGroups: s.layerGroups.map((g) => (g.id === groupId ? { ...g, name } : g)),
      })),
    removeLayerGroup: (groupId) =>
      set((s) => {
        const membership = { ...s.layerGroupMembership };
        for (const [layerId, gid] of Object.entries(membership)) {
          if (gid === groupId) delete membership[layerId];
        }
        return {
          layerGroups: s.layerGroups.filter((g) => g.id !== groupId),
          layerGroupMembership: membership,
        };
      }),
    toggleGroupCollapsed: (groupId) =>
      set((s) => ({
        layerGroups: s.layerGroups.map((g) =>
          g.id === groupId ? { ...g, collapsed: !g.collapsed } : g,
        ),
      })),
    assignLayersToGroup: (layerIds, groupId) =>
      set((s) => {
        if (groupId && !s.layerGroups.some((g) => g.id === groupId)) return s;
        const membership = { ...s.layerGroupMembership };
        for (const layerId of layerIds) {
          if (groupId == null) delete membership[layerId];
          else membership[layerId] = groupId;
        }
        return { layerGroupMembership: membership };
      }),
    pruneLayerGroups: (validLayerIds) =>
      set((s) => {
        const membership: Record<string, string> = {};
        let changed = false;
        for (const [layerId, gid] of Object.entries(s.layerGroupMembership)) {
          if (validLayerIds.has(layerId)) membership[layerId] = gid;
          else changed = true;
        }
        // 空组保留（用户组织结构不因图层清空而消失）。
        return changed ? { layerGroupMembership: membership } : s;
      }),
    resetLayerGroups: () =>
      set({
        layerGroups: [],
        layerGroupMembership: {},
        selectedLayerIds: [],
        lockedLayerIds: [],
        isolatedLayerId: null,
        isolatedFrom: null,
      }),

    /* ─── Artifact Selection ─── */
    selectedArtifactId: null,
    setSelectedArtifactId: (id) => set({ selectedArtifactId: id }),

    /* ─── Comparison ─── */
    comparison: EMPTY_COMPARISON,
    // enter 保留上次分割位置（再次进入不跳回缺省）。
    enterComparison: (patch) =>
      set((s) => ({
        comparison: { ...EMPTY_COMPARISON, position: s.comparison.position, ...patch, active: true },
      })),
    updateComparison: (patch) =>
      set((s) => (s.comparison.active ? { comparison: { ...s.comparison, ...patch } } : s)),
    exitComparison: () =>
      set((s) => ({ comparison: { ...EMPTY_COMPARISON, position: s.comparison.position } })),
  };
}
