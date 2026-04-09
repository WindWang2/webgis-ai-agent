'use client';

import React, { createContext, useContext, useState, useCallback } from 'react';

export interface TaskStep {
  id: string;
  tool: string;
  stepIndex: number;
  status: 'running' | 'completed' | 'failed';
  result?: unknown;
  hasGeojson?: boolean;
  error?: string;
}

export interface TaskState {
  id: string;
  steps: TaskStep[];
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  stepCount?: number;
  summary?: string;
}

export interface TaskContextValue {
  currentTask: TaskState | null;
  handleTaskStart: (taskId: string) => void;
  handleStepStart: (taskId: string, stepId: string, stepIndex: number, tool: string) => void;
  handleStepResult: (taskId: string, stepId: string, tool: string, result: unknown, hasGeojson: boolean) => void;
  handleStepError: (taskId: string, stepId: string, error: string) => void;
  handleTaskComplete: (taskId: string, stepCount: number, summary: string) => void;
  handleTaskError: (taskId: string, error: string) => void;
  handleTaskCancelled: (taskId: string) => void;
  clearTask: () => void;
}

export const TaskContext = createContext<TaskContextValue | undefined>(undefined);

export function TaskProvider({ children }: { children: React.ReactNode }) {
  const [currentTask, setCurrentTask] = useState<TaskState | null>(null);

  const handleTaskStart = useCallback((taskId: string) => {
    console.log('[Task] Starting task:', taskId);
    setCurrentTask({
      id: taskId,
      steps: [],
      status: 'running',
    });
  }, []);

  const handleStepStart = useCallback((taskId: string, stepId: string, stepIndex: number, tool: string) => {
    console.log('[Task] Step started:', taskId, stepId, stepIndex, tool);
    setCurrentTask((prev) => {
      if (!prev || prev.id !== taskId) return prev;
      const newStep: TaskStep = {
        id: stepId,
        tool,
        stepIndex,
        status: 'running',
      };
      return {
        ...prev,
        steps: [...prev.steps, newStep],
      };
    });
  }, []);

  const handleStepResult = useCallback(
    (taskId: string, stepId: string, tool: string, result: unknown, hasGeojson: boolean) => {
      console.log('[Task] Step result:', taskId, stepId, tool);
      setCurrentTask((prev) => {
        if (!prev || prev.id !== taskId) return prev;
        return {
          ...prev,
          steps: prev.steps.map((step) =>
            step.id === stepId
              ? { ...step, status: 'completed' as const, result, hasGeojson, tool }
              : step
          ),
        };
      });
    },
    []
  );

  const handleStepError = useCallback((taskId: string, stepId: string, error: string) => {
    console.log('[Task] Step error:', taskId, stepId, error);
    setCurrentTask((prev) => {
      if (!prev || prev.id !== taskId) return prev;
      return {
        ...prev,
        steps: prev.steps.map((step) =>
          step.id === stepId ? { ...step, status: 'failed' as const, error } : step
        ),
      };
    });
  }, []);

  const handleTaskComplete = useCallback(
    (taskId: string, stepCount: number, summary: string) => {
      console.log('[Task] Task completed:', taskId, stepCount, summary);
      setCurrentTask((prev) => {
        if (!prev || prev.id !== taskId) return prev;
        return {
          ...prev,
          status: 'completed',
          stepCount,
          summary,
        };
      });
    },
    []
  );

  const handleTaskError = useCallback((taskId: string, error: string) => {
    console.log('[Task] Task error:', taskId, error);
    setCurrentTask((prev) => {
      if (!prev || prev.id !== taskId) return prev;
      return {
        ...prev,
        status: 'failed',
        summary: error,
      };
    });
  }, []);

  const handleTaskCancelled = useCallback((taskId: string) => {
    console.log('[Task] Task cancelled:', taskId);
    setCurrentTask((prev) => {
      if (!prev || prev.id !== taskId) return prev;
      return {
        ...prev,
        status: 'cancelled',
      };
    });
  }, []);

  const clearTask = useCallback(() => {
    console.log('[Task] Clearing task');
    setCurrentTask(null);
  }, []);

  return (
    <TaskContext.Provider
      value={{
        currentTask,
        handleTaskStart,
        handleStepStart,
        handleStepResult,
        handleStepError,
        handleTaskComplete,
        handleTaskError,
        handleTaskCancelled,
        clearTask,
      }}
    >
      {children}
    </TaskContext.Provider>
  );
}

export default TaskProvider;

export function useTask() {
  const context = useContext(TaskContext);
  if (context === undefined) {
    throw new Error('useTask must be used within a TaskProvider');
  }
  return context;
}