'use client';

import { useState, useEffect } from 'react';
import { Loader2, Check, X, ChevronDown, ChevronUp, Ban } from 'lucide-react';
import { cancelTask } from '@/lib/api/task';
import { TaskState } from '@/lib/contexts/task-context';

interface TaskProgressProps {
  task: TaskState;
  originalRequest?: string;
}

function StepIcon({ status }: { status: 'running' | 'completed' | 'failed' }) {
  switch (status) {
    case 'running':
      return <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-400" />;
    case 'completed':
      return <Check className="h-3.5 w-3.5 text-green-500" />;
    case 'failed':
      return <X className="h-3.5 w-3.5 text-red-500" />;
    default:
      return null;
  }
}

export function TaskProgress({ task, originalRequest = '' }: TaskProgressProps) {
  const [expanded, setExpanded] = useState(true);
  const [cancelling, setCancelling] = useState(false);

  // 任务完成或失败时自动折叠
  useEffect(() => {
    if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
      setExpanded(false);
    }
  }, [task.status]);

  const completedSteps = task.steps.filter((s) => s.status === 'completed').length;
  const totalSteps = task.stepCount || task.steps.length || 1;
  const progressPercent = Math.min((completedSteps / totalSteps) * 100, 100);

  const handleCancel = async () => {
    if (cancelling) return;
    setCancelling(true);
    try {
      await cancelTask(task.id);
    } catch (error) {
      console.error('[TaskProgress] Cancel failed:', error);
    } finally {
      setCancelling(false);
    }
  };

  // 截断标题到30字
  const displayTitle = originalRequest.length > 30
    ? originalRequest.substring(0, 30) + '...'
    : originalRequest;

  const statusLabels: Record<string, string> = {
    running: '进行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  };

  return (
    <div className="my-3 rounded-lg border border-cyan-500/30 bg-cyan-950/30 overflow-hidden">
      {/* 可折叠标题栏 */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-cyan-950/50 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-medium text-cyan-300 shrink-0">
            {statusLabels[task.status] || '未知'}
          </span>
          <span className="text-sm text-white truncate">{displayTitle}</span>
        </div>
        {expanded ? (
          <ChevronUp className="h-4 w-4 text-cyan-400 shrink-0" />
        ) : (
          <ChevronDown className="h-4 w-4 text-cyan-400 shrink-0" />
        )}
      </button>

      {/* 可折叠内容区 */}
      {expanded && (
        <div className="px-3 pb-3">
          {/* 步骤列表 */}
          <div className="space-y-1.5 mb-3">
            {task.steps.map((step) => (
              <div
                key={step.id}
                className="flex items-center gap-2 text-xs"
              >
                <StepIcon status={step.status} />
                <span className="text-gray-300">{step.tool}</span>
                {step.error && (
                  <span className="text-red-400 truncate max-w-48">
                    : {step.error}
                  </span>
                )}
              </div>
            ))}
            {task.steps.length === 0 && (
              <div className="text-xs text-gray-500">等待开始...</div>
            )}
          </div>

          {/* 进度条和取消按钮 */}
          <div className="flex items-center gap-3">
            <div className="flex-1 h-1.5 bg-gray-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-cyan-400 to-blue-500 transition-all duration-300"
                style={{ width: `${progressPercent}%` }}
              />
            </div>
            <span className="text-xs text-cyan-300 whitespace-nowrap">
              {completedSteps}/{totalSteps}
            </span>
            {task.status === 'running' && (
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-red-500/20 text-red-400 hover:bg-red-500/30 disabled:opacity-50 transition-colors"
              >
                <Ban className="h-3 w-3" />
                <span>{cancelling ? '取消中...' : '取消'}</span>
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}