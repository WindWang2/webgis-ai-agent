'use client';

/**
 * ResultList — bounded, session-scoped list of captured analysis results.
 * Master half of the Results tab master-detail. Keyboard-navigable (native
 * buttons), dense, status-forward.
 */
import { ClipboardList } from 'lucide-react';
import clsx from 'clsx';
import { familyLabel } from '@/lib/results/families';
import type { AnalysisResult } from '@/lib/results/types';
import { StatusBadge } from '@/components/shared/status-badge';
import { EmptyState } from '@/components/shared/empty-state';

interface ResultListProps {
  results: AnalysisResult[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

const STATUS_LABEL: Record<string, string> = {
  completed: '已完成',
  failed: '失败',
  partial: '部分完成',
  warning: '含告警',
  running: '运行中',
  unknown: '未知',
};

function formatTime(ms?: number): string {
  if (!ms) return '';
  try {
    return new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

export function ResultList({ results, selectedId, onSelect }: ResultListProps) {
  if (results.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-4">
        <EmptyState
          icon={ClipboardList}
          title="暂无分析结果"
          description="完成一次空间分析后，结果将自动出现在此处，供你查看输入、指标、输出与地图联动。"
        />
      </div>
    );
  }

  return (
    <ul aria-label="分析结果列表" className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-2">
      {results.map((r) => {
        const selected = r.id === selectedId;
        const hasLayer = r.outputs[0]?.hasLayer;
        return (
          <li key={r.id}>
            <button
              type="button"
              aria-current={selected ? 'true' : undefined}
              onClick={() => onSelect(r.id)}
              className={clsx(
                'flex w-full flex-col gap-1 rounded-md border px-2.5 py-2 text-left transition-colors',
                selected
                  ? 'border-[var(--agent-accent,#16a34a)]/50 bg-[var(--theme-bg-hover)]'
                  : 'border-transparent hover:bg-[var(--theme-bg-hover)]',
              )}
            >
              <div className="flex items-center gap-1.5">
                <span className="truncate text-[13px] font-medium text-[var(--theme-text-primary)]">{r.toolLabel}</span>
                <StatusBadge status={r.status} label={STATUS_LABEL[r.status] ?? r.status} />
                <span className="ml-auto shrink-0 text-[10.5px] text-[var(--theme-text-muted)]">{formatTime(r.capturedAt)}</span>
              </div>
              <div className="flex items-center gap-1.5 text-[11px] text-[var(--theme-text-muted)]">
                <span>{familyLabel(r.family)}</span>
                {hasLayer ? (
                  <span className="inline-flex items-center gap-0.5 rounded bg-[var(--theme-bg-subtle)] px-1 py-0.5 text-[10px]">
                    已挂载图层
                  </span>
                ) : null}
                {r.warnings.length > 0 ? (
                  <span className="inline-flex items-center gap-0.5 rounded bg-amber-500/10 px-1 py-0.5 text-[10px] text-amber-600 dark:text-amber-300">
                    {r.warnings.length} 条告警
                  </span>
                ) : null}
              </div>
              {r.summary ? (
                <span className="line-clamp-2 text-[12px] text-[var(--theme-text-secondary)]">{r.summary}</span>
              ) : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export default ResultList;
