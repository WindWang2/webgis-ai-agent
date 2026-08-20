'use client';

import React, { useEffect, useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import type { GraduatedLegendSpec } from '@/lib/map-kit/types';
import { LegendCard, formatLegendValue } from './legend-card';

interface Props {
  spec: GraduatedLegendSpec;
  onFilterChange?: (visibleBreaks: number[][]) => void;
}

export function GraduatedLegend({ spec, onFilterChange }: Props) {
  const { field, breaks, palette_colors } = spec;
  const classCount = Math.max(0, breaks.length - 1);
  const [visible, setVisible] = useState<boolean[]>(() => new Array(classCount).fill(true));

  const specKey = `${field}:${breaks.join(',')}`;
  useEffect(() => {
    setVisible(new Array(classCount).fill(true));
    if (onFilterChange) {
      // Spec identity changed (new breaks / field) — clear any stale range filter
      // that was built from the old breaks so legend and map stay in sync.
      // Empty array + classCount check covers the degenerate case safely.
      const allRanges =
        classCount > 0
          ? breaks.slice(0, -1).map((v, i) => [v, breaks[i + 1]] as number[])
          : [];
      onFilterChange(allRanges);
    }
  }, [specKey]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!breaks || breaks.length < 2) return null;

  const toggle = (idx: number) => {
    const next = [...visible];
    next[idx] = !next[idx];
    setVisible(next);
    if (onFilterChange) {
      const ranges = breaks.slice(0, -1)
        .map((v, i) => (next[i] ? [v, breaks[i + 1]] : null))
        .filter((r): r is number[] => r !== null);
      onFilterChange(ranges);
    }
  };

  return (
    <LegendCard field={field} kind="数据驱动专题渲染">
      <div className="mb-1 flex justify-between text-micro tabular-nums text-map-chrome-ink-muted">
        <span>{formatLegendValue(breaks[0])}</span>
        <span>{formatLegendValue(breaks[breaks.length - 1])}</span>
      </div>
      <div className="space-y-0.5">
        {breaks.slice(0, -1).map((val, idx) => {
          const nextVal = breaks[idx + 1];
          const colorIdx = Math.min(idx, palette_colors.length - 1);
          const isVisible = visible[idx];
          const rangeLabel = `${formatLegendValue(val)} — ${formatLegendValue(nextVal)}`;
          return (
            <div
              key={idx}
              role="button"
              tabIndex={0}
              // aria-pressed carries the class's visibility, which the eye icon
              // showed visually but never announced.
              aria-pressed={isVisible}
              aria-label={rangeLabel}
              className={`group flex min-h-row-sm cursor-pointer items-center justify-between gap-2 rounded-sm px-1 transition-colors hover:bg-surface-hover ${!isVisible ? 'opacity-50' : ''}`}
              onClick={() => toggle(idx)}
              onKeyDown={(e) => {
                // Space as well as Enter: role="button" must honour both.
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  toggle(idx);
                }
              }}
            >
              <div className="flex min-w-0 items-center gap-2">
                <div
                  aria-hidden
                  className="h-icon-sm w-icon-sm shrink-0 rounded-xs ring-1 ring-inset ring-map-chrome-border"
                  style={{ backgroundColor: palette_colors[colorIdx] }}
                />
                <span className="truncate text-meta tabular-nums text-map-chrome-ink">
                  {rangeLabel}
                </span>
              </div>
              {isVisible ? (
                <Eye aria-hidden className="h-icon-sm w-icon-sm shrink-0 text-status-accent" />
              ) : (
                <EyeOff aria-hidden className="h-icon-sm w-icon-sm shrink-0 text-ink-disabled" />
              )}
            </div>
          );
        })}
      </div>
    </LegendCard>
  );
}
