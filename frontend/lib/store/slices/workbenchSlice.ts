'use client';
import type { StateCreator } from 'zustand';
import type { HudState } from '../hud-types';
import { canReparentGroup, type WorkbenchDocV5 } from '@/lib/workbench/doc';

/**
 * Workbench slice — Workbench V4 统一工作区投影（Goal C / Wave 1）→
 * V5 组织态运行时载体（W1/W3）。
 *
 * 设计边界（ADR-0104 + V5 修订）：
 * - 本 slice 是 **UI projection / transient state**，绝不承载地图语义真相：
 *   权威地图内容仍以 MapSpec / backend contract 为准（session-cursor 镜像 +
 *   user-mutation CAS 串行链），图层语义分组仍在 `Layer.group`。这里只放
 *   「工作台怎么摆、当前在操作什么」这类会话级 UI 状态。
 * - 单一 store：本 slice 并入 useHudStore（不建第二 store、不建第二 truth）。
 * - V5：分组树/成员/锁/模式升级为 WorkbenchDocV5（嵌套树），经
 *   patch_workbench_state 意图持久化到 MapSpec `workbench` 分支。本 slice
 *   仍是运行时投影（setWorkbenchDoc 水合 / buildWorkbenchDoc 提交），
 *   持久化真相在 backend —— 不在这里建 localStorage 影子真相。
 *
 * 子域：
 * - mode：Explore / Analyze / Compose 三模式（Wave 3）。模式只改变面板组合
 *   / 工具栏 / 上下文控件，不复制地图状态；每个模式记忆自己的 active tab。
 * - layer selection：多选（批量操作 / Wave 2 Layer Workspace 的选择真相）。
 * - layer groups：用户分组树（V5 起支持嵌套，深度上限见 doc.ts）。
 * - artifact selection：当前关注的产物（results/chart）。
 * - comparison：对比工作区状态（Wave 8；两视图同步策略可配置）。
 */

/** 工作台模式词表（封闭）。 */
export type WorkbenchMode = 'explore' | 'analyze' | 'compose';

export const WORKBENCH_MODES: readonly WorkbenchMode[] = ['explore', 'analyze', 'compose'];

/** 模式 → 左栏 tab 组合（封闭词表投影；chat 恒在场）。 */
export const MODE_TABS: Record<WorkbenchMode, readonly string[]> = {
  // V9（ADR-0145）：market / modelops 智能资产面板对全部模式可见（尾部追加）；
  // lakehouse（ADR-0141）仅 explore / analyze 可见（与智能资产面板并集共存）。
  // ADR-0142：ops 运维控制台对全部模式可见（尾部追加，居词表尾）。
  explore: ['chat', 'layers', 'data_sources', 'project', 'tasks', 'lakehouse', 'market', 'modelops', 'ops'],
  analyze: ['chat', 'analysis', 'results', 'layers', 'tasks', 'lakehouse', 'market', 'modelops', 'ops'],
  compose: ['chat', 'components', 'export_layout', 'exports', 'layers', 'market', 'modelops', 'ops'],
};

/** 模式切换来源（Agent 不应静默切换 —— 需要可发现的回执）。 */
export type WorkbenchModeOrigin = 'user' | 'agent';

export interface LayerGroupEntity {
  id: string;
  name: string;
  collapsed: boolean;
  /** V5 嵌套树：null/缺省 = 根组（V4 迁移期兼容：实体可缺字段）。 */
  parentId?: string | null;
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
  /** agent 切换前用户所在模式（一键返回的目标；用户切换时不清除）。 */
  userModeBeforeAgent: WorkbenchMode;
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

  /* ─── Layer Groups（V5 嵌套组织树，doc 持久化见 lib/workbench/doc.ts）─── */
  layerGroups: LayerGroupEntity[];
  layerGroupMembership: Record<string, string>;
  createLayerGroup: (name: string, parentId?: string | null) => string;
  renameLayerGroup: (groupId: string, name: string) => void;
  removeLayerGroup: (groupId: string) => void;
  toggleGroupCollapsed: (groupId: string) => void;
  /** 嵌套：把组移动到新父下（null = 提为根）；环/超深被拒绝并保持原状。 */
  moveLayerGroup: (groupId: string, newParentId: string | null) => boolean;
  /** 把图层指派到组（groupId = null → 移出组）。 */
  assignLayersToGroup: (layerIds: string[], groupId: string | null) => void;
  /** 会话切换 / 图层删除后的组成员清理（防 stale id 垃圾）。 */
  pruneLayerGroups: (validLayerIds: ReadonlySet<string>) => void;
  resetLayerGroups: () => void;
  /* ─── Workbench Doc V5（W3 持久化水合）─── */
  /** 从恢复的 doc 一次性水合组织态（组/成员/锁/模式）；非法 doc 被拒绝。 */
  hydrateWorkbenchDoc: (doc: WorkbenchDocV5 | null) => boolean;

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
  return {
    /* ─── Mode ─── */
    mode: 'explore',
    modeActiveTab: { explore: 'chat', analyze: 'analysis', compose: 'components' },
    modeOrigin: null as WorkbenchModeOrigin | null,
    userModeBeforeAgent: 'explore' as WorkbenchMode,
    // Review R1（architecture MAJOR-3）：tab 协调上收 store —— 切模式即把
    // activeLeftTab 落到该模式的记忆 tab（无记忆用组合首个），保证
    // activeLeftTab ∈ MODE_TABS[mode] 对用户与 agent 路径同时成立
    // （此前只有 NavRail 用户路径做协调，agent set_mode 会留下界外 tab）。
    setWorkbenchMode: (mode, origin = 'user') =>
      set((s) => {
        if (s.mode === mode) {
          return s.modeOrigin === origin ? s : { modeOrigin: origin };
        }
        const remembered = s.modeActiveTab[mode] ?? MODE_TABS[mode][0];
        return {
          mode,
          modeOrigin: origin,
          activeLeftTab: remembered as typeof s.activeLeftTab,
          leftPanelOpen: true,
          // agent 切换时记录用户此前所在模式（MINOR-8：一键返回语义）。
          userModeBeforeAgent: origin === 'agent' ? s.mode : s.userModeBeforeAgent,
        };
      }),

    /* ─── Layer Selection ─── */
    selectedLayerIds: [],
    // Review R1（MINOR-8）：等值 no-op 门（与 #739/#1078 纪律一致 ——
    // 冗余调用不得翻转数组身份churn订阅者）。
    setSelectedLayerIds: (ids) =>
      set((s) => {
        const next = Array.from(new Set(ids));
        if (
          s.selectedLayerIds.length === next.length
          && s.selectedLayerIds.every((id) => next.includes(id))
        ) {
          return s;
        }
        return { selectedLayerIds: next };
      }),
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
    createLayerGroup: (name, parentId = null) => {
      groupSeq += 1;
      const id = `wg-${groupSeq}`;
      set((s) => {
        // 父不存在 → 落根（与投影孤儿兜底语义一致）。
        const validParent =
          parentId != null && s.layerGroups.some((g) => g.id === parentId) ? parentId : null;
        return {
          layerGroups: [...s.layerGroups, { id, name, collapsed: false, parentId: validParent }],
        };
      });
      return id;
    },
    renameLayerGroup: (groupId, name) =>
      set((s) => ({
        layerGroups: s.layerGroups.map((g) => (g.id === groupId ? { ...g, name } : g)),
      })),
    removeLayerGroup: (groupId) =>
      set((s) => {
        const removed = s.layerGroups.find((g) => g.id === groupId);
        if (!removed) return s;
        // 嵌套语义：被删组的子组提升到被删组的父（不孤儿、不级联删除）。
        const promoted = s.layerGroups.map((g) =>
          g.parentId === groupId ? { ...g, parentId: removed.parentId } : g,
        );
        const membership = { ...s.layerGroupMembership };
        for (const [layerId, gid] of Object.entries(membership)) {
          if (gid === groupId) delete membership[layerId];
        }
        return {
          layerGroups: promoted.filter((g) => g.id !== groupId),
          layerGroupMembership: membership,
        };
      }),
    toggleGroupCollapsed: (groupId) =>
      set((s) => ({
        layerGroups: s.layerGroups.map((g) =>
          g.id === groupId ? { ...g, collapsed: !g.collapsed } : g,
        ),
      })),
    moveLayerGroup: (groupId, newParentId) => {
      let allowed = false;
      set((s) => {
        if (!s.layerGroups.some((g) => g.id === groupId)) return s;
        if (
          newParentId != null
          && !s.layerGroups.some((g) => g.id === newParentId)
        ) {
          return s;
        }
        // canReparentGroup 覆盖自环/子孙环/深度上限；非法保持原状并返回 false。
        if (!canReparentGroup(s.layerGroups, groupId, newParentId)) return s;
        allowed = true;
        return {
          layerGroups: s.layerGroups.map((g) =>
            g.id === groupId ? { ...g, parentId: newParentId } : g,
          ),
        };
      });
      return allowed;
    },
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
        const next: Partial<HudState> = {};
        if (changed) next.layerGroupMembership = membership;
        // Review R1（perf MINOR-7）：删除层的死 id 同步出选择/锁定 ——
        // 批量条「已选 N」不再虚计，锁定守卫不再遍历死 id。
        const selected = s.selectedLayerIds.filter((id) => validLayerIds.has(id));
        if (selected.length !== s.selectedLayerIds.length) next.selectedLayerIds = selected;
        const locked = s.lockedLayerIds.filter((id) => validLayerIds.has(id));
        if (locked.length !== s.lockedLayerIds.length) next.lockedLayerIds = locked;
        return Object.keys(next).length > 0 ? next : s;
      }),
    // Review R1（MAJOR-2）：会话切换同样退出对比 —— primary/secondary 是
    // 旧会话的图层族 id，叠在新会话地图上是死引用；产物选择同理。
    resetLayerGroups: () =>
      set((s) => ({
        layerGroups: [],
        layerGroupMembership: {},
        selectedLayerIds: [],
        lockedLayerIds: [],
        isolatedLayerId: null,
        isolatedFrom: null,
        comparison: { ...EMPTY_COMPARISON, position: s.comparison.position },
        selectedArtifactId: null,
      })),

    /* ─── Workbench Doc V5（W3）─── */
    hydrateWorkbenchDoc: (doc) => {
      if (!doc) return false;
      set({
        layerGroups: doc.groups.map((g) => ({ ...g })),
        layerGroupMembership: { ...doc.membership },
        lockedLayerIds: [...doc.lockedLayerIds],
      });
      // 模式经既有 setWorkbenchMode 协调 activeLeftTab（doc.mode 与用户当前
      // 模式一致时为 no-op，不打断用户上下文）。
      if (doc.mode !== get().mode) get().setWorkbenchMode(doc.mode, 'user');
      return true;
    },

    /* ─── Artifact Selection ─── */
    selectedArtifactId: null,
    setSelectedArtifactId: (id) => set({ selectedArtifactId: id }),

    /* ─── Comparison ─── */
    comparison: EMPTY_COMPARISON,
    // enter 保留上次分割位置（再次进入不跳回缺省）。
    enterComparison: (patch) =>
      set((s) => ({
        // Review R1（MINOR-12）：kind 与 position 同样保留（用户偏好不被重置）。
        comparison: {
          ...EMPTY_COMPARISON,
          position: s.comparison.position,
          kind: s.comparison.kind,
          ...patch,
          active: true,
        },
      })),
    updateComparison: (patch) =>
      set((s) => (s.comparison.active ? { comparison: { ...s.comparison, ...patch } } : s)),
    exitComparison: () =>
      set((s) => ({ comparison: { ...EMPTY_COMPARISON, position: s.comparison.position } })),
  };
}

/**
 * 从 store 投影出可持久化的 WorkbenchDocV5（W3 提交通道的载荷）。
 * 纯函数：只读入参，不触 store。
 */
export function buildWorkbenchDoc(state: {
  layerGroups?: LayerGroupEntity[];
  layerGroupMembership?: Record<string, string>;
  lockedLayerIds?: string[];
  mode?: WorkbenchMode;
}): WorkbenchDocV5 {
  return {
    version: 5,
    groups: (state.layerGroups ?? []).map((g) => ({
      id: g.id,
      name: g.name,
      collapsed: g.collapsed,
      parentId: g.parentId ?? null,
    })),
    membership: { ...(state.layerGroupMembership ?? {}) },
    lockedLayerIds: [...(state.lockedLayerIds ?? [])],
    mode: state.mode ?? 'explore',
  };
}
