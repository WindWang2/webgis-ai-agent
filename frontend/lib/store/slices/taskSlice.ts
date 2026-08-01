/**
 * Task slice — Explorer 后台任务列表 +（已退场的）chat-task 清理钩子。
 *
 * 原 chat-task 跟踪器（currentTask + 7 个生命周期 action）是死代码：SSE handler
 * 从不 dispatch taskStart/stepStart/stepResult，currentTask 恒为 null，TaskProgress
 * 从不渲染。已删除（ADR-0022）。clearTask 保留为 no-op —— use-workspace-session.ts
 * 在切换 session 时调用它做防御性重置。
 *
 * Explorer Tasks 区块是活的：explorer_progress SSE 事件驱动 updateExplorerTask。
 */
import type { StateCreator } from 'zustand';
import type { HudState } from '../hud-types';

export const createTaskSlice: StateCreator<HudState, [], [], Partial<HudState>> = (set) => ({
  /* ─── Chat Task（已退场，保留清理钩子）─── */
  // No-op: the chat-task tracker is gone (ADR-0022), but use-workspace-session.ts
  // still calls clearTask() on session-switch as a defensive reset. Kept as an
  // empty function so those call sites stay valid without touching the hook.
  clearTask: () => {},

  /* ─── Explorer Tasks ─── */
  explorerTasks: [],
  addExplorerTask: (task) =>
    set((state) => ({
      explorerTasks: [...state.explorerTasks, task],
    })),
  updateExplorerTask: (taskId, updates) =>
    set((state) => ({
      explorerTasks: state.explorerTasks.map((t) =>
        t.taskId === taskId ? { ...t, ...updates, updatedAt: Date.now() } : t,
      ),
    })),
  removeExplorerTask: (taskId) =>
    set((state) => ({
      explorerTasks: state.explorerTasks.filter((t) => t.taskId !== taskId),
    })),
});
