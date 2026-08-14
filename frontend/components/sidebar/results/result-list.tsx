'use client';

/**
 * ResultList — bounded, session-scoped list of captured analysis results.
 * Master half of the Results tab master-detail. Keyboard-navigable (native
 * buttons), dense, status-forward.
 *
 * UI V4 — dense two-line rows with the layers-tab border-l-2 edge treatment
 * instead of stacked cards: tool/status/time on the first line, family · map
 * linkage · warning tally · summary on the second. Selected ≠ hover (accent
 * edge + surface-selected vs plain hover), per the nav-rail contract.
 */
import { useEffect, useRef } from 'react';
import { ClipboardList, Layers, TriangleAlert } from 'lucide-react';
import clsx from 'clsx';
import { familyLabel } from '@/lib/results/families';
import type { AnalysisResult } from '@/lib/results/types';
import { StatusBadge } from '@/components/shared/status-badge';
import { EmptyState } from '@/components/shared/empty-state';

interface ResultListProps {
  results: AnalysisResult[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** Row id whose focus Back should restore (drill-in focus contract). */
  restoreFocusId?: string | null;
  /** Called after `restoreFocusId` was consumed (whether the row was found). */
  onRestoredFocus?: () => void;
}

function formatTime(ms?: number): string {
  if (!ms) return '';
  try {
    return new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

export function ResultList({ results, selectedId, onSelect, restoreFocusId, onRestoredFocus }: ResultListProps) {
  const listRef = useRef<HTMLUListElement>(null);

  // Focus contract: after Back, focus returns to the row that was opened
  // (consumed exactly once — unrelated re-renders never steal focus).
  useEffect(() => {
    if (!restoreFocusId) return;
    const list = listRef.current;
    const btn = list?.querySelector(
      `button[data-result-id="${CSS.escape(restoreFocusId)}"]`,
    );
    if (btn instanceof HTMLButtonElement) {
      btn.focus();
    } else {
      // The row is gone (removed from the detail view, or evicted by the
      // 50-result cap while open) — land on the list container instead of
      // dropping keyboard focus to <body>.
      list?.focus();
    }
    onRestoredFocus?.();
  }, [restoreFocusId, results, onRestoredFocus]);

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
    <ul
      ref={listRef}
      tabIndex={-1}
      aria-label="分析结果列表"
      className="flex min-h-0 flex-1 flex-col overflow-y-auto py-1 outline-none focus:outline-none"
    >
      {results.map((r) => {
        const selected = r.id === selectedId;
        const hasLayer = r.outputs[0]?.hasLayer;
        return (
          <li key={r.id}>
            <button
              type="button"
              data-result-id={r.id}
              aria-current={selected ? 'true' : undefined}
              onClick={() => onSelect(r.id)}
              className={clsx(
                'flex w-full flex-col gap-0.5 border-l-2 px-panel py-1 text-left transition-colors',
                selected
                  ? 'border-l-status-accent bg-surface-selected'
                  : 'border-l-transparent hover:bg-surface-hover',
              )}
            >
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="truncate text-body font-medium text-ink">{r.toolLabel}</span>
                <StatusBadge status={r.status} />
                <span className="ml-auto shrink-0 font-mono text-caption tabular-nums text-ink-muted">
                  {formatTime(r.capturedAt)}
                </span>
              </span>
              <span className="flex min-w-0 items-center gap-2 text-meta text-ink-muted">
                <span className="shrink-0">{familyLabel(r.family)}</span>
                {hasLayer ? (
                  <span className="inline-flex shrink-0 items-center gap-0.5 text-status-accent">
                    <Layers size={10} aria-hidden />
                    图层
                  </span>
                ) : null}
                {r.warnings.length > 0 ? (
                  <span className="inline-flex shrink-0 items-center gap-0.5 text-status-warning">
                    <TriangleAlert size={10} aria-hidden />
                    {r.warnings.length} 条告警
                  </span>
                ) : null}
                {r.summary ? (
                  <span className="min-w-0 truncate text-ink-muted" title={r.summary}>
                    {r.summary}
                  </span>
                ) : null}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export default ResultList;
