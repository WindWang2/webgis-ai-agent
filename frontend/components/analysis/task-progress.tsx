'use client';

import { useState, useEffect } from 'react';
import { Loader2, CheckCircle2, XCircle, Clock, Play } from 'lucide-react';
import { ANALYSIS_TYPES } from './analysis-types';

export interface Task {
  id: string;
  type: string;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  progress: number;
  createdAt: string;
  completedAt?: string;
  errorMessage?: string;
  resultLayerId?: string;
  params: Record<string, unknown>;
}

interface TaskProgressProps {
  tasks?: Task[];
  onTaskComplete?: (task: Task) => void;
  refreshInterval?: number;
}

const STATUS_CONFIG = {
  queued: { icon: Clock, color: 'text-yellow-600', bg: 'bg-yellow-50', text: '排队中' },
  processing: { icon: Loader2, color: 'text-blue-600', bg: 'bg-blue-50', text: '处理中' },
  completed: { icon: CheckCircle2, color: 'text-green-600', bg: 'bg-green-50', text: '完成' },
  failed: { icon: XCircle, color: 'text-red-600', bg: 'bg-red-50', text: '失败' },
};

export function TaskProgress({ tasks: initialTasks, onTaskComplete, refreshInterval = 3000 }: TaskProgressProps) {
  const [tasks, setTasks] = useState<Task[]>(initialTasks || []);
  const [loading, setLoading] = useState(false);

  const fetchTasks = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/analysis/tasks');
      if (!response.ok) throw new Error('获取任务列表失败');
      const data = await response.json();
      const newTasks = data.tasks || [];

      // Check for completed tasks
      newTasks.forEach(task => {
        if (task.status === 'completed' || task.status === 'failed') {
          const existingTask = tasks.find(t => t.id === task.id);
          if (!existingTask || existingTask.status !== task.status) {
            onTaskComplete?.(task);
          }
        }
      });

      setTasks(newTasks);
    } catch (err) {
      console.error('Failed to fetch tasks:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!initialTasks) {
      fetchTasks();
      const interval = setInterval(fetchTasks, refreshInterval);
      return () => clearInterval(interval);
    }
  }, [initialTasks, refreshInterval]);

  const handleRetry = async (taskId: string) => {
    try {
      const response = await fetch(`/api/analysis/tasks/${taskId}/retry`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error('重试失败');
      fetchTasks();
    } catch (err) {
      console.error('Failed to retry task:', err);
    }
  };

  const handleDelete = async (taskId: string) => {
    if (!confirm('确定要删除此任务吗？')) return;

    try {
      const response = await fetch(`/api/analysis/tasks/${taskId}`, {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error('删除失败');
      setTasks(prev => prev.filter(t => t.id !== taskId));
    } catch (err) {
      console.error('Failed to delete task:', err);
    }
  };

  const getTypeName = (typeId: string) => {
    return ANALYSIS_TYPES.find(t => t.id === typeId)?.name || typeId;
  };

  const formatTime = (isoString: string) => {
    return new Date(isoString).toLocaleString('zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  const activeTasks = tasks.filter(t => t.status === 'queued' || t.status === 'processing');
  const completedTasks = tasks.filter(t => t.status === 'completed' || t.status === 'failed');

  return (
    <div className="w-full mt-6">
      <h4 className="text-sm font-semibold text-gray-700 mb-3">任务进度</h4>

      {loading && !initialTasks && (
        <div className="flex items-center justify-center py-4">
          <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
        </div>
      )}

      {tasks.length === 0 && !loading ? (
        <p className="text-sm text-gray-500 text-center py-4">暂无任务</p>
      ) : (
        <div className="space-y-3">
          {/* Active Tasks */}
          {activeTasks.length > 0 && (
            <div className="space-y-2">
              {activeTasks.map(task => {
                const StatusIcon = STATUS_CONFIG[task.status].icon;
                const config = STATUS_CONFIG[task.status];

                return (
                  <div
                    key={task.id}
                    className={`p-3 rounded-lg border ${config.bg} border-transparent`}
                  >
                    <div className="flex items-start gap-2">
                      <StatusIcon
                        className={`h-4 w-4 flex-shrink-0 mt-0.5 ${config.color} ${
                          task.status === 'processing' ? 'animate-spin' : ''
                        }`}
                      />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-900">
                          {getTypeName(task.type)}
                        </p>
                        <p className="text-xs text-gray-500">
                          {formatTime(task.createdAt)}
                        </p>
                        {task.status === 'processing' && (
                          <div className="mt-2">
                            <div className="flex items-center justify-between text-xs text-gray-600 mb-1">
                              <span>处理中...</span>
                              <span>{Math.round(task.progress)}%</span>
                            </div>
                            <div className="w-full bg-gray-200 rounded-full h-1.5">
                              <div
                                className="bg-blue-600 h-1.5 rounded-full transition-all"
                                style={{ width: `${task.progress}%` }}
                              />
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Completed Tasks */}
          {completedTasks.length > 0 && (
            <div className="space-y-2 pt-2 border-t border-gray-200">
              <p className="text-xs text-gray-500">已完成任务</p>
              {completedTasks.map(task => {
                const StatusIcon = STATUS_CONFIG[task.status].icon;
                const config = STATUS_CONFIG[task.status];

                return (
                  <div
                    key={task.id}
                    className={`p-3 rounded-lg border ${config.bg} border-transparent flex items-start gap-2`}
                  >
                    <StatusIcon className={`h-4 w-4 flex-shrink-0 mt-0.5 ${config.color}`} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium text-gray-900">
                          {getTypeName(task.type)}
                        </p>
                        <span className={`text-xs ${config.color}`}>{config.text}</span>
                      </div>
                      <p className="text-xs text-gray-500">
                        {formatTime(task.createdAt)}
                        {task.completedAt && ` - ${formatTime(task.completedAt)}`}
                      </p>
                      {task.errorMessage && (
                        <p className="text-xs text-red-600 mt-1">{task.errorMessage}</p>
                      )}
                    </div>
                    {task.status === 'failed' && (
                      <button
                        onClick={() => handleRetry(task.id)}
                        className="p-1 text-blue-600 hover:bg-blue-100 rounded"
                        title="重试"
                      >
                        <Play className="h-3 w-3" />
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(task.id)}
                      className="p-1 text-gray-400 hover:text-red-600 rounded"
                      title="删除"
                    >
                      <XCircle className="h-3 w-3" />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
