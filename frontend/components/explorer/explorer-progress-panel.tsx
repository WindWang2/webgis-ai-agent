"use client";

import { useHudStore } from "@/lib/store/useHudStore";
import type { ExplorerTask, ExplorerStatus } from "@/lib/types/explorer";

const STAGE_LABELS: Record<string, string> = {
  discover: "数据发现",
  fetch: "内容下载",
  parse: "结构化解析",
  geocode: "地理编码",
  validate: "质量验证",
};

// #518: 原实现用 text-white/* 等暗色玻璃样式，浅色主题下文字几乎不可读。
// 改为主题 token（ink/status-*），深浅主题都自适应。
const STATUS_COLORS: Record<ExplorerStatus, string> = {
  idle: "text-status-neutral",
  discovering: "text-status-info",
  fetching: "text-status-info",
  parsing: "text-status-info",
  geocoding: "text-status-info",
  validating: "text-status-info",
  decision_required: "text-status-warning",
  completed: "text-status-success",
  failed: "text-status-critical",
  aborted: "text-status-neutral",
};

function TaskCard({ task }: { task: ExplorerTask }) {
  const progress = task.progress || 0;
  const stageLabel = STAGE_LABELS[task.stage] || task.stage;

  return (
    <div className="rounded-lg border border-edge-subtle bg-surface-sunken p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-ink">{task.query}</span>
        <span className={`text-xs ${STATUS_COLORS[task.status]}`}>
          {task.status === "completed" ? "完成" :
           task.status === "failed" ? "失败" :
           task.status === "aborted" ? "已中止" :
           `${stageLabel}...`}
        </span>
      </div>

      {task.status !== "completed" && task.status !== "failed" && (
        <div className="mt-2">
          <div
            className="h-1.5 rounded-full bg-status-neutral-soft overflow-hidden"
            role="progressbar"
            aria-valuenow={progress}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${stageLabel} 进度`}
          >
            <div
              className="h-1.5 rounded-full bg-status-info transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
          <div className="mt-1 flex justify-between text-xs text-ink-muted">
            <span>{stageLabel}</span>
            <span>{progress}%</span>
          </div>
        </div>
      )}

      {task.rowCount !== undefined && task.status === "completed" && (
        <div className="mt-2 text-xs text-ink-muted">
          共 {task.rowCount} 条数据
          {task.successRate !== undefined && ` · 编码成功率 ${(task.successRate * 100).toFixed(0)}%`}
        </div>
      )}

      {task.error && (
        <div className="mt-2 text-xs text-status-critical">{task.error}</div>
      )}
    </div>
  );
}

export function ExplorerProgressPanel() {
  const tasks = useHudStore((s) => s.explorerTasks);

  if (tasks.length === 0) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-disabled">
        深度搜索
      </h3>
      {tasks.map((task) => (
        <TaskCard key={task.taskId} task={task} />
      ))}
    </div>
  );
}
