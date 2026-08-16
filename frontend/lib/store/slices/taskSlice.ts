/**
 * Task slice — Explorer 后台任务列表 +（已退场的）chat-task 清理钩子。
 *
 * 原 chat-task 跟踪器（currentTask + 7 个生命周期 action）是死代码：SSE handler
 * 从不 dispatch taskStart/stepStart/stepResult，currentTask 恒为 null，TaskProgress
 * 从不渲染。已删除（ADR-0022）。clearTask 保留为 no-op —— use-workspace-session.ts
 * 在切换 session 时调用它做防御性重置。
 *
 * Explorer Tasks 区块是活的：explorer_progress SSE 事件驱动 updateExplorerTask。
 *
 * #548: explorerTasks 是 session 作用域集合，但此前无上限、也不随会话切换清空：
 * SSE writer 按 Celery task_id 去重，而每个任务都是全新 id —— 去重只防同任务
 * 重复，从不上限列表，旧会话的探索卡片会泄漏进新会话的任务 tab。与兄弟 slice
 * 的上限模式（MAX_OPS_LOG / MAX_RESULTS / MAX_ANNOTATIONS）对齐。
 */
import type { StateCreator } from 'zustand';
import type { HudState } from '../hud-types';
import type { ExplorerTask } from '@/lib/types/explorer';

/** #548: 单张卡片 = 单个 Celery task_id（全新增量），无界追加会无限增长。 */
export const MAX_EXPLORER_TASKS = 50;

const TERMINAL_EXPLORER_STATUSES = new Set(['completed', 'failed', 'aborted']);

/** 超限时驱逐一条：优先丢最旧的终态（completed/failed/aborted）卡片，让在飞
 * 任务保持可见；无终态时退回丢最旧（resultsSlice 同款有界淘汰风格）。 */
function evictExplorerTask(tasks: ExplorerTask[]): ExplorerTask[] {
  const terminalIdx = tasks.findIndex((t) => TERMINAL_EXPLORER_STATUSES.has(t.status));
  if (terminalIdx !== -1) return tasks.filter((_, i) => i !== terminalIdx);
  return tasks.slice(1);
}

export const createTaskSlice: StateCreator<HudState, [], [], Partial<HudState>> = (set) => ({
  /* ─── Chat Task（已退场，保留清理钩子）─── */
  // No-op: the chat-task tracker is gone (ADR-0022), but use-workspace-session.ts
  // still calls clearTask() on session-switch as a defensive reset. Kept as an
  // empty function so those call sites stay valid without touching the hook.
  clearTask: () => {},

  /* ─── Explorer Tasks ─── */
  explorerTasks: [],
  addExplorerTask: (task) =>
    set((state) => {
      const next = [...state.explorerTasks, task];
      return {
        explorerTasks: next.length > MAX_EXPLORER_TASKS ? evictExplorerTask(next) : next,
      };
    }),
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
  clearExplorerTasks: () => set({ explorerTasks: [] }),
});
