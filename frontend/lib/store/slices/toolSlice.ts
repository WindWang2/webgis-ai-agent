'use client';

/**
 * Tool slice — V7 全局地图工具状态（审计 §5）。
 *
 * 此前「当前工具」散落三处（MapPanel 本地 measureMode/brushSelectActive、
 * uiSlice.activeTool 无生产消费者、AnalysisTab 本地 state），导致：
 *   - 工具无互斥（测量与框选可同时激活）；
 *   - 无法按状态推导可用性（工具对谁都永远可点）；
 *   - 会话切换不重置（测量模式跨会话残留）；
 *   - 状态条/Agent 无法观察「用户当前拿着什么工具」。
 *
 * 本 slice 是唯一工具真相：activeMapTool 单字段天然互斥；可用性由组件
 * 经 selector 从图层/草图状态推导（canActivate 语义见 map-toolbar-hud）；
 * 会话切换经 clearToolState 复位。
 */
import type { StateCreator } from 'zustand';
import type { HudState } from '../hud-types';

/** 地图交互工具词表（封闭）。null = 无激活工具（漫游/点选）。 */
export type MapToolId =
  | 'measure_distance'
  | 'measure_area'
  | 'brush_select'
  | 'draw_point'
  | 'draw_line'
  | 'draw_polygon'
  | 'edit_vertices'
  | 'delete_feature'
  // G14：真实图层编辑（需先选定 editTarget）
  | 'split_feature';

export const MAP_TOOLS: readonly MapToolId[] = [
  'measure_distance',
  'measure_area',
  'brush_select',
  'draw_point',
  'draw_line',
  'draw_polygon',
  'edit_vertices',
  'delete_feature',
  'split_feature',
];

/** G14：真实图层编辑目标（HUD 行 ↔ ref 绑定；null = 未进入编辑）。 */
export interface EditTarget {
  layerId: string;
  refId: string;
}

export interface ToolSlice {
  activeMapTool: MapToolId | null;
  /** 激活工具；再次激活同一工具 = 取消（toggle 语义，单字段天然互斥）。 */
  setActiveMapTool: (tool: MapToolId | null) => void;
  /** 绘制/顶点编辑时的顶点吸附。 */
  snappingEnabled: boolean;
  toggleSnapping: () => void;
  /** 草图有未保存变更（save/cancel 纪律的脏标记）。 */
  sketchDirty: boolean;
  setSketchDirty: (dirty: boolean) => void;
  /** G14：真实图层编辑目标；设置时若草图工具在持先退出（互斥）。 */
  editTarget: EditTarget | null;
  setEditTarget: (target: EditTarget | null) => void;
  /** 会话切换复位（工具激活态/脏标记/编辑目标不跨会话；吸附是偏好，保留）。 */
  clearToolState: () => void;
}

export const createToolSlice: StateCreator<HudState, [], [], ToolSlice> = (set) => ({
  activeMapTool: null,
  setActiveMapTool: (tool) => set({ activeMapTool: tool }),
  snappingEnabled: true,
  toggleSnapping: () => set((s) => ({ snappingEnabled: !s.snappingEnabled })),
  sketchDirty: false,
  setSketchDirty: (dirty) => set({ sketchDirty: dirty }),
  editTarget: null,
  setEditTarget: (target) =>
    set((s) => ({
      editTarget: target,
      // 退出编辑目标时清结构编辑工具；进入时清草图类工具（手势互斥）
      activeMapTool: target
        ? (s.activeMapTool && ['draw_point', 'draw_line', 'draw_polygon', 'edit_vertices', 'delete_feature'].includes(s.activeMapTool)
            ? null
            : s.activeMapTool)
        : (s.activeMapTool === 'split_feature' ? null : s.activeMapTool),
    })),
  clearToolState: () => set({ activeMapTool: null, sketchDirty: false, editTarget: null }),
});
