'use client';

import React from 'react';
import { ChartCore } from '@/components/chat/chart-core';
import { adaptChartData } from '@/lib/chart-adapter';

/**
 * StoryDashboard —— 章节联动数据看板（ADR-0196）。
 *
 * 活跃章节引用的 widget 高亮（脉冲环，prefers-reduced-motion 降级为静态
 * 边框），其余以弱化态常驻 —— 图文同频联动的「表」半边。
 */

export interface DashboardWidget {
  id: string;
  kind: 'chart' | 'table' | 'kpi' | 'stats';
  title: string;
  data: Record<string, unknown>;
}

export interface StoryDashboardProps {
  widgets: DashboardWidget[];
  highlightedIds?: string[];
  className?: string;
}

export function StoryDashboard({
  widgets,
  highlightedIds = [],
  className,
}: StoryDashboardProps): React.ReactElement | null {
  if (widgets.length === 0) return null;
  return (
    <div
      data-testid="story-dashboard"
      className={`flex flex-col gap-2 ${className ?? ''}`}
    >
      {widgets.map((w) => {
        const highlighted = highlightedIds.includes(w.id);
        const chart = w.kind === 'chart' ? adaptChartData(w.data) : null;
        return (
          <div
            key={w.id}
            data-testid={`story-widget-${w.id}`}
            data-story-widget-highlight={highlighted ? 'true' : undefined}
            className={`rounded-lg border p-3 transition-all duration-500 bg-surface-raised/90 ${
              highlighted
                ? 'border-status-info story-pulse-ring'
                : 'border-edge-subtle opacity-55'
            }`}
          >
            <header className="pb-1 text-caption font-medium uppercase tracking-wide text-ink-muted">
              {w.title || w.id}
            </header>
            {chart ? (
              <ChartCore chart={chart} height={160} />
            ) : (
              <pre className="m-0 whitespace-pre-wrap font-mono text-meta text-ink-secondary">
                {w.kind === 'kpi' && typeof w.data.value !== 'undefined'
                  ? String(w.data.value)
                  : JSON.stringify(w.data)}
              </pre>
            )}
          </div>
        );
      })}
    </div>
  );
}
