'use client';

import { useState, useEffect } from 'react';
import { Loader2, Check, X, ChevronDown, ChevronUp, Ban, Compass } from 'lucide-react';
import { cancelTask } from '@/lib/api/task';
import { TaskState } from '@/lib/contexts/task-context';

interface TaskProgressProps {
  task: TaskState;
  originalRequest?: string;
}

// 探险步进图标
function StepIcon({ status }: { status: 'running' | 'completed' | 'failed' }) {
  switch (status) {
    case 'running':
      return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />;
    case 'completed':
      return <Check className="h-3.5 w-3.5 text-success" />;
    case 'failed':
      return <X className="h-3.5 w-3.5 text-error" />;
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

  const statusLabel: Record<string, string> = {
    running: '进行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  };

  return (
    <div className="my-3 rounded-lg border border-border bg-card/50 overflow-hidden">
      {/* 可折叠标题栏 - 探险日志风格 */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-left hover:bg-card transition-colors"
      >
        <div className="flex items-center gap-3 min-w-0">
          <Compass className="h-4 w-4 text-primary shrink-0" />
          <span className="text-xs font-medium text-primary shrink-0">
            {statusLabel[task.status] || '未知'}
          </span>
          <span className="text-sm text-foreground truncate italic">{displayTitle}</span>
        </div>
        {expanded ? (
          <ChevronUp className="h-4 w-4 text-muted-foreground shrink-0" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" />
        )}
      </button>

      {/* 可折叠内容区 */}
      {expanded && (
        <div className="px-4 pb-3">
          {/* 步骤列表 - 探险路线风格 */}
          <div className="space-y-2 mb-4 pl-1">
            {task.steps.map((step) => (
              <div
                key={step.id}
                className="flex items-center gap-2.5 text-xs"
              >
                <StepIcon status={step.status} />
                <span className="text-muted-foreground">{step.tool}</span>
                {step.error && (
                  <span className="text-error truncate max-w-48">
                    ✕ {step.error}
                  </span>
                )}
              </div>
            ))}
            {task.steps.length === 0 && (
              <div className="text-xs text-muted-foreground italic">待命...</div>
            )}
          </div>

          {/* 进度条和取消按钮 - 指南针风格 */}
          <div className="flex items-center gap-3">
            <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-primary to-accent transition-all duration-500"
                style={{ width: `${progressPercent}%` }}
              />
            </div>
            <span className="text-xs text-primary font-mono whitespace-nowrap">
              ◉ {completedSteps}/{totalSteps}
            </span>
            {task.status === 'running' && (
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className="flex items-center gap-1 px-2.5 py-1 rounded text-xs bg-error/10 text-error hover:bg-error/20 disabled:opacity-50 transition-colors border border-error/30"
              >
                <Ban className="h-3 w-3" />
                <span>{cancelling ? '终止中...' : '终止'}</span>
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}