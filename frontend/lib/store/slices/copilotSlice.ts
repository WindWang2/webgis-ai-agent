'use client';

/**
 * Copilot slice — 混合能动性画布协同状态（ADR-0194）。
 *
 * 三条状态轴：
 *   - copilotTool        画布意图工具（框选/手绘圈选/多边形套索）——与
 *                        toolSlice.activeMapTool（地图编辑工具）刻意分离：
 *                        copilot 是「向 Agent 表达意图」，不写用户草图图层。
 *   - copilotHighlight   最近一次手势的虚线高亮环（屏幕像素坐标）。
 *   - copilotWidgets     Agent 下发的生成式微 UI 卡片（ui_action.mount_widget，
 *                        mount 前 isWidgetSpecSafe 已校验；FIFO ≤ 4）。
 *   - staged/report      交互上报：stagedCopilotEnvelope 随下一轮 stream 捎带；
 *                        reportLatencyMs 是「手势完成→信封离开前端」的同步
 *                        耗时遥测（预算 < 100ms，spec §5）。
 */
import type { StateCreator } from 'zustand';
import type { HudState } from '../hud-types';
import type { WidgetSpec } from '@/lib/copilot/affordance';
import type { SpatialAffordanceEnvelope } from '@/lib/copilot/affordance';

/** 画布 copilot 工具词表（封闭）。 */
export type CopilotToolId = 'box_select' | 'freehand_lasso' | 'polygon_lasso';

export interface CopilotHighlight {
  /** 屏幕像素环（组件覆层坐标系）。 */
  ring: [number, number][];
  kind: CopilotToolId;
  /** WGS84 环（sketch-box widget 裁决回传用；工具投影失败可缺席）。 */
  lnglatRing?: [number, number][];
}

export interface MountedCopilotWidget {
  spec: WidgetSpec;
  mountedAt: number;
}

export const MAX_COPILOT_WIDGETS = 4;

export interface CopilotSlice {
  copilotTool: CopilotToolId | null;
  setCopilotTool: (tool: CopilotToolId | null) => void;
  copilotHighlight: CopilotHighlight | null;
  setCopilotHighlight: (highlight: CopilotHighlight | null) => void;
  copilotWidgets: MountedCopilotWidget[];
  pushCopilotWidget: (spec: WidgetSpec) => void;
  dismissCopilotWidget: (widgetId: string) => void;
  /** 随下一轮 /chat/stream 请求捎带的信封（消费即清空，防重发）。 */
  stagedCopilotEnvelope: SpatialAffordanceEnvelope | null;
  stageCopilotEnvelope: (envelope: SpatialAffordanceEnvelope) => void;
  consumeStagedCopilotEnvelope: () => SpatialAffordanceEnvelope | null;
  copilotReportLatencyMs: number | null;
  recordCopilotReportLatency: (ms: number) => void;
  /** 会话切换复位（工具/高亮/卡片是会话级意图态）。 */
  clearCopilotState: () => void;
}

export const createCopilotSlice: StateCreator<HudState, [], [], CopilotSlice> = (set, get) => ({
  copilotTool: null,
  setCopilotTool: (tool) => set({ copilotTool: tool }),
  copilotHighlight: null,
  setCopilotHighlight: (highlight) => set({ copilotHighlight: highlight }),
  copilotWidgets: [],
  pushCopilotWidget: (spec) =>
    set((s) => {
      if (s.copilotWidgets.some((w) => w.spec.widget_id === spec.widget_id)) {
        return s; // 同 id 重复挂载（resume 重放）幂等。
      }
      const next = [...s.copilotWidgets, { spec, mountedAt: Date.now() }];
      return { copilotWidgets: next.slice(-MAX_COPILOT_WIDGETS) };
    }),
  dismissCopilotWidget: (widgetId) =>
    set((s) => ({
      copilotWidgets: s.copilotWidgets.filter((w) => w.spec.widget_id !== widgetId),
    })),
  stagedCopilotEnvelope: null,
  stageCopilotEnvelope: (envelope) =>
    set((s) => {
      // 信封合并：未消费的既有 staged 信封 actions 并入新信封（上限内），
      // 避免「上报即发但 turn 未启动」窗口内的动作丢失。
      const prev = s.stagedCopilotEnvelope;
      if (!prev) return { stagedCopilotEnvelope: envelope };
      const merged = {
        ...envelope,
        actions: [...prev.actions, ...envelope.actions].slice(-12),
      };
      return { stagedCopilotEnvelope: merged };
    }),
  consumeStagedCopilotEnvelope: () => {
    const envelope = get().stagedCopilotEnvelope;
    if (envelope) set({ stagedCopilotEnvelope: null });
    return envelope;
  },
  copilotReportLatencyMs: null,
  recordCopilotReportLatency: (ms) => set({ copilotReportLatencyMs: ms }),
  clearCopilotState: () =>
    set({
      copilotTool: null,
      copilotHighlight: null,
      copilotWidgets: [],
      stagedCopilotEnvelope: null,
    }),
});
