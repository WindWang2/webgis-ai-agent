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

const STATUS_COLORS: Record<ExplorerStatus, string> = {
  idle: "text-gray-400",
  discovering: "text-blue-400",
  fetching: "text-blue-400",
  parsing: "text-blue-400",
  geocoding: "text-blue-400",
  validating: "text-blue-400",
  decision_required: "text-yellow-400",
  completed: "text-green-400",
  failed: "text-red-400",
  aborted: "text-gray-400",
};

function TaskCard({ task }: { task: ExplorerTask }) {
  const progress = task.progress || 0;
  const stageLabel = STAGE_LABELS[task.stage] || task.stage;

  return (
    <div className="rounded-lg border border-white/10 bg-white/5 p-3 backdrop-blur-sm">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-white/90">{task.query}</span>
        <span className={`text-xs ${STATUS_COLORS[task.status]}`}>
          {task.status === "completed" ? "完成" :
           task.status === "failed" ? "失败" :
           task.status === "aborted" ? "已中止" :
           `${stageLabel}...`}
        </span>
      </div>

      {task.status !== "completed" && task.status !== "failed" && (
        <div className="mt-2">
          <div className="h-1.5 rounded-full bg-white/10">
            <div
              className="h-1.5 rounded-full bg-blue-400 transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
          <div className="mt-1 flex justify-between text-xs text-white/50">
            <span>{stageLabel}</span>
            <span>{progress}%</span>
          </div>
        </div>
      )}

      {task.rowCount !== undefined && task.status === "completed" && (
        <div className="mt-2 text-xs text-white/60">
          共 {task.rowCount} 条数据
          {task.successRate !== undefined && ` · 编码成功率 ${(task.successRate * 100).toFixed(0)}%`}
        </div>
      )}

      {task.error && (
        <div className="mt-2 text-xs text-red-400">{task.error}</div>
      )}
    </div>
  );
}

export function ExplorerProgressPanel() {
  const tasks = useHudStore((s) => s.explorerTasks);

  if (tasks.length === 0) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-white/40">
        深度搜索
      </h3>
      {tasks.map((task) => (
        <TaskCard key={task.taskId} task={task} />
      ))}
    </div>
  );
}
