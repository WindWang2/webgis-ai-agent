'use client';

import { useState, useEffect } from 'react';
import { Loader2, Check, X, ChevronDown, ChevronUp, Ban, Compass, AlertCircle } from 'lucide-react';
import { cancelTask } from '@/lib/api/task';
import { TaskState } from '@/lib/contexts/task-context';

interface TaskProgressProps {
  task: TaskState;
  originalRequest?: string;
}

// 探险步进图标 - 使用更精致的样式
function StepIcon({ status }: { status: 'running' | 'completed' | 'failed' }) {
  switch (status) {
    case 'running':
      return (
        <div className="relative flex items-center justify-center">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
          <div className="absolute inset-0 bg-primary/20 rounded-full blur-sm scale-150 animate-pulse" />
        </div>
      );
    case 'completed':
      return <div className="bg-success/20 p-0.5 rounded-full"><Check className="h-3 w-3 text-success stroke-[3]" /></div>;
    case 'failed':
      return <div className="bg-error/20 p-0.5 rounded-full"><X className="h-3 w-3 text-error stroke-[3]" /></div>;
    default:
      return <div className="h-3.5 w-3.5 rounded-full border border-border bg-muted/30" />;
  }
}

export function TaskProgress({ task, originalRequest = '' }: TaskProgressProps) {
  const [expanded, setExpanded] = useState(true);
  const [cancelling, setCancelling] = useState(false);

  // 任务完成或取消时自动折叠，但失败时保持展开
  useEffect(() => {
    if (task.status === 'completed' || task.status === 'cancelled') {
      const timer = setTimeout(() => setExpanded(false), 3000); // 延长可见时间到3秒
      return () => clearTimeout(timer);
    } else if (task.status === 'failed') {
      setExpanded(true); // 失败任务强制保持展开
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

  const statusLabel: Record<string, { label: string; color: string }> = {
    running: { label: '航道探索中', color: 'text-primary' },
    completed: { label: '探索达成', color: 'text-success' },
    failed: { label: '遭遇暗礁', color: 'text-error' },
    cancelled: { label: '航程中止', color: 'text-muted-foreground' },
  };

  const currentStatus = statusLabel[task.status] || { label: '待命', color: 'text-muted-foreground' };

  return (
    <div className={`my-3 rounded-lg border transition-all duration-300 ${
      task.status === 'failed' ? 'border-error/40 bg-error/5 shadow-lg' : 'border-border bg-card/40'
    } backdrop-blur-sm overflow-hidden`}>
      {/* 标题栏 */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-card/60 transition-colors"
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className={`flex h-8 w-8 items-center justify-center rounded-full border border-border/50 bg-background-secondary shadow-inner`}>
            <Compass className={`h-4.5 w-4.5 ${currentStatus.color} ${task.status === 'running' ? 'animate-[spin_4s_linear_infinite]' : ''}`} />
          </div>
          <div className="flex flex-col min-w-0">
            <span className={`text-[10px] uppercase tracking-widest font-bold ${currentStatus.color}`}>
              {currentStatus.label}
            </span>
            <span className="text-sm text-foreground/90 truncate font-medium">
              任务 ID: {task.id.split('-')[0]}...
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
            {task.status === 'running' && (
               <span className="text-[10px] font-mono text-primary/70 bg-primary/10 px-1.5 py-0.5 rounded border border-primary/20">
                 {progressPercent.toFixed(0)}%
               </span>
            )}
            {expanded ? (
              <ChevronUp className="h-4 w-4 text-muted-foreground/60" />
            ) : (
              <ChevronDown className="h-4 w-4 text-muted-foreground/60" />
            )}
        </div>
      </button>

      {/* 内容区 */}
      <div className={`transition-all duration-300 ease-in-out ${expanded ? 'max-h-96 opacity-100' : 'max-h-0 opacity-0'} overflow-hidden`}>
        <div className="px-4 pb-4 pt-1 space-y-4">
          {/* 进度轨道 */}
          <div className="relative">
             <div className="h-1 bg-muted rounded-full overflow-hidden shadow-inner">
                <div
                  className={`h-full transition-all duration-1000 ease-out ${
                    task.status === 'failed' ? 'bg-error' : 'bg-gradient-to-r from-primary/60 via-primary to-accent'
                  }`}
                  style={{ width: `${progressPercent}%` }}
                />
             </div>
             {/* 粒子光点效果 (仅运行中) */}
             {task.status === 'running' && progressPercent < 100 && (
                <div 
                  className="absolute top-0 w-4 h-1 bg-white/40 blur-sm rounded-full animate-[pulse_1s_infinite]"
                  style={{ left: `calc(${progressPercent}% - 8px)` }}
                />
             )}
          </div>

          {/* 步骤列表 */}
          <div className="space-y-3 pl-1">
            {task.steps.map((step, idx) => (
              <div
                key={step.id}
                className={`flex items-start gap-3 text-xs group transition-all duration-300 ${
                  step.status === 'running' ? 'opacity-100 scale-105' : 'opacity-80'
                }`}
              >
                <div className="mt-0.5 relative z-10">
                   <StepIcon status={step.status} />
                   {idx < task.steps.length - 1 && (
                     <div className="absolute top-4 left-[6px] w-[1px] h-6 bg-border/40" />
                   )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex justify-between items-center bg-card/30 rounded px-2 py-1 border border-border/30 hover:border-primary/20 transition-all">
                    <span className="font-mono text-[11px] text-foreground/80 tracking-tight">[{step.tool}]</span>
                    {step.status === 'completed' && <span className="text-[10px] text-success/70 font-mono">完毕</span>}
                  </div>
                  {step.error && (
                    <div className="mt-1.5 p-2 bg-error/10 border border-error/20 rounded text-error text-[11px] leading-relaxed animate-in fade-in slide-in-from-top-1">
                      <div className="flex items-center gap-1.5 font-bold mb-0.5">
                        <AlertCircle className="h-3 w-3" /> 观测异常
                      </div>
                      {step.error}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {task.steps.length === 0 && (
              <div className="py-2 text-center border border-dashed border-border/40 rounded-lg">
                <span className="text-xs text-muted-foreground/60 italic">航道规划中...</span>
              </div>
            )}
          </div>

          {/* 控制项 */}
          {task.status === 'running' && (
            <div className="flex justify-end border-t border-border/20 pt-3">
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className="group flex items-center gap-2 px-3 py-1.5 rounded-md text-[11px] font-bold tracking-wider uppercase
                           bg-error/5 text-error border border-error/20 hover:bg-error hover:text-white transition-all shadow-sm active:scale-95"
              >
                <Ban className={`h-3.5 w-3.5 ${cancelling ? 'animate-spin' : 'group-hover:rotate-90 transition-transform'}`} />
                <span>{cancelling ? '抛锚中...' : '中止航程'}</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}