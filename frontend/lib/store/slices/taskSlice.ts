/**
 * Task slice — 当前 chat 任务的运行轨迹 + Explorer 后台任务列表。
 *
 * 关注点：随 LLM 流式生成、tool 调用前进的「单任务」状态机。
 */
import type { StateCreator } from 'zustand';
import type { HudState } from '../hud-types';

export const createTaskSlice: StateCreator<HudState, [], [], Partial<HudState>> = (set) => ({
  /* ─── 当前 chat Task ─── */
  currentTask: null,

  taskStart: (taskId) =>
    set({ currentTask: { id: taskId, steps: [], status: 'running' } }),

  stepStart: (taskId, stepId, stepIndex, tool) =>
    set((s) => {
      if (!s.currentTask || s.currentTask.id !== taskId) return s;
      return {
        currentTask: {
          ...s.currentTask,
          steps: [
            ...s.currentTask.steps,
            { id: stepId, tool, stepIndex, status: 'running', startedAt: Date.now() },
          ],
        },
      };
    }),

  stepResult: (taskId, stepId, tool, result, hasGeojson, snapshot) =>
    set((s) => {
      if (!s.currentTask || s.currentTask.id !== taskId) return s;
      return {
        currentTask: {
          ...s.currentTask,
          steps: s.currentTask.steps.map((step) =>
            step.id === stepId
              ? {
                  ...step,
                  status: 'completed' as const,
                  result,
                  hasGeojson,
                  tool,
                  geojsonSnapshot: snapshot,
                  completedAt: Date.now(),
                }
              : step,
          ),
        },
      };
    }),

  stepError: (taskId, stepId, error) =>
    set((s) => {
      if (!s.currentTask || s.currentTask.id !== taskId) return s;
      return {
        currentTask: {
          ...s.currentTask,
          steps: s.currentTask.steps.map((step) =>
            step.id === stepId
              ? { ...step, status: 'failed' as const, error, completedAt: Date.now() }
              : step,
          ),
        },
      };
    }),

  taskComplete: (taskId, stepCount, summary) =>
    set((s) => {
      if (!s.currentTask || s.currentTask.id !== taskId) return s;
      return {
        currentTask: { ...s.currentTask, status: 'completed', stepCount, summary },
      };
    }),

  taskError: (taskId, error) =>
    set((s) => {
      if (!s.currentTask || s.currentTask.id !== taskId) return s;
      return {
        currentTask: { ...s.currentTask, status: 'failed', summary: error },
      };
    }),

  taskCancelled: (taskId) =>
    set((s) => {
      if (!s.currentTask || s.currentTask.id !== taskId) return s;
      return { currentTask: { ...s.currentTask, status: 'cancelled' } };
    }),

  clearTask: () => set({ currentTask: null }),

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
