'use client';

/**
 * useHudStore — 全局 HUD store 组合入口。
 *
 * 实现按"slice 模式"拆分（M3 重构）：
 *   - layersSlice    图层 / 编辑 / 过程层 / 分析资产
 *   - taskSlice      当前 chat 任务 / Explorer 任务
 *   - settingsSlice  Skills / RAG / 地图样式 / LLM 配置
 *   - uiSlice        viewport / 面板可见性 / 主题色 / 日志 / 感知 / 导出布局
 *
 * 单文件巨型 store 拆为多个 ~80-100 行的 slice 文件后：
 *   - 关注点更清晰，每个 slice 一个职责轴
 *   - 单元测试可以只 mock 涉及到的 slice
 *   - DEMO 数据 / 默认配置已移到 lib/constants/demo.ts
 *
 * **公共契约不变**：
 *   - `useHudStore`、`HudState` 等类型导出保持原样（TaskStep/TaskState 已随
 *     死代码 chat-task 跟踪器一并移除，见 ADR-0022）
 *   - persist key、partialize 字段集合保持原样
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import type { HudState, LeftTab } from './hud-types';
import { createLayersSlice } from './slices/layersSlice';
import { createSettingsSlice } from './slices/settingsSlice';
import { createTaskSlice } from './slices/taskSlice';
import { createUiSlice } from './slices/uiSlice';

// Type re-exports（保留旧导入习惯）
export type {
  HudState,
  AiStatus,
  LeftTab,
  SettingsTab,
} from './hud-types';

// Demo / 默认数据从原文件搬到 constants/demo.ts；保留 re-export 以免外部代码崩
export {
  DEMO_MESSAGES,
  DEMO_LAYERS,
  DEMO_RAG,
  DEMO_EXPORTS,
  DEMO_OPS_LOG,
  DEMO_CAUSAL_CHAIN,
} from '../constants/demo';

export const useHudStore = create<HudState>()(
  persist(
    (...a) => ({
      ...createLayersSlice(...a),
      ...createTaskSlice(...a),
      ...createSettingsSlice(...a),
      ...createUiSlice(...a),
    }) as HudState,
    {
      name: 'geoagent-settings',
      partialize: (state) => ({
        skills: state.skills,
        ragConfig: state.ragConfig,
        mapStyles: state.mapStyles,
        baseLayer: state.baseLayer,
        // 审计 F29：accentColor 之前未持久化 -> 用户选的颜色刷新后丢失。
        accentColor: state.accentColor,
        // SECURITY: 去除 apiKey 后再持久化，避免明文写入 localStorage
        // （XSS / 浏览器扩展 / DevTools 可读）。
        llmConfigFull: state.llmConfigFull
          ? { ...state.llmConfigFull, apiKey: '' }
          : state.llmConfigFull,
        // 注意：刻意 *不* 持久化 layers - 单个分析结果可能数百 MB GeoJSON，
        // 会爆掉 localStorage 5–10 MB 配额并连带破坏 setItem (静默丢失其它字段)。
      }),
    },
  ),
);

export interface HudSnapshot {
  hudOpen: boolean;
  activeLeftTab: LeftTab;
  activeTool: string | null;
  timestamp: number;
}

export class EmbodiedHudEngine {
  private static historyStack: HudSnapshot[] = [];
  private static redoStack: HudSnapshot[] = [];
  private static maxHistory = 20;

  public static getState(): HudState {
    return useHudStore.getState();
  }

  public static recordSnapshot(): HudSnapshot {
    const state = useHudStore.getState();
    const snapshot: HudSnapshot = {
      hudOpen: state.hudOpen,
      activeLeftTab: state.activeLeftTab,
      activeTool: state.activeTool ?? null,
      timestamp: Date.now(),
    };
    this.historyStack.push(snapshot);
    if (this.historyStack.length > this.maxHistory) {
      this.historyStack.shift();
    }
    this.redoStack = [];
    return snapshot;
  }

  public static undo(): boolean {
    if (this.historyStack.length === 0) return false;
    const previous = this.historyStack.pop();
    if (!previous) return false;
    const state = useHudStore.getState();
    this.redoStack.push({
      hudOpen: state.hudOpen,
      activeLeftTab: state.activeLeftTab,
      activeTool: state.activeTool ?? null,
      timestamp: Date.now(),
    });
    useHudStore.setState({
      hudOpen: previous.hudOpen,
      activeLeftTab: previous.activeLeftTab,
      activeTool: previous.activeTool,
    });
    return true;
  }

  public static redo(): boolean {
    if (this.redoStack.length === 0) return false;
    const next = this.redoStack.pop();
    if (!next) return false;
    const state = useHudStore.getState();
    this.historyStack.push({
      hudOpen: state.hudOpen,
      activeLeftTab: state.activeLeftTab,
      activeTool: state.activeTool ?? null,
      timestamp: Date.now(),
    });
    useHudStore.setState({
      hudOpen: next.hudOpen,
      activeLeftTab: next.activeLeftTab,
      activeTool: next.activeTool,
    });
    return true;
  }

  public static toggleLeftDrawer(tab?: LeftTab): void {
    this.recordSnapshot();
    const state = useHudStore.getState();
    if (tab && state.activeLeftTab === tab && state.hudOpen) {
      state.setHudOpen(false);
    } else if (tab) {
      state.setActiveLeftTab(tab);
      state.setHudOpen(true);
    } else {
      state.setHudOpen(!state.hudOpen);
    }
  }

  public static setActiveTool(toolName: string | null): void {
    this.recordSnapshot();
    useHudStore.setState({ activeTool: toolName });
  }

  public static resetState(): void {
    this.recordSnapshot();
    useHudStore.setState({
      activeTool: null,
      hudOpen: false,
    });
  }
}
