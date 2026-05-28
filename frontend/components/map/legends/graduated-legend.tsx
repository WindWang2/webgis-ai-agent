'use client';

import React, { useEffect, useState } from 'react';
import { Info, Eye, EyeOff } from 'lucide-react';
import type { GraduatedLegendSpec } from '@/lib/map-kit/types';

interface Props {
  spec: GraduatedLegendSpec;
  onFilterChange?: (visibleBreaks: number[][]) => void;
}

const formatNum = (n: number) => {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(0) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(0) + 'k';
  return String(Math.round(n));
};

export function GraduatedLegend({ spec, onFilterChange }: Props) {
  const { field, breaks, palette_colors } = spec;
  const classCount = Math.max(0, breaks.length - 1);
  const [visible, setVisible] = useState<boolean[]>(() => new Array(classCount).fill(true));

  useEffect(() => {
    setVisible(new Array(classCount).fill(true));
  }, [classCount]);

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
    <div className="bg-card/90 backdrop-blur-md border border-border p-4 rounded-xl shadow-2xl min-w-[200px] animate-in slide-in-from-right-4 duration-500">
      <div className="flex items-center gap-2 mb-3 border-b border-border pb-2">
        <div className="p-1 bg-primary/10 rounded-md">
          <Info className="h-3.5 w-3.5 text-primary" />
        </div>
        <div className="flex flex-col">
          <span className="text-[14px] uppercase font-bold tracking-widest text-muted-foreground/80">图例说明</span>
          <span className="text-xs font-semibold text-foreground truncate max-w-[140px]" title={field}>
            字段: {field}
          </span>
        </div>
      </div>
      <div className="flex justify-between text-[15px] text-muted-foreground/70 mb-1 px-1">
        <span>{formatNum(breaks[0])}</span>
        <span>{formatNum(breaks[breaks.length - 1])}</span>
      </div>
      <div className="space-y-2">
        {breaks.slice(0, -1).map((val, idx) => {
          const nextVal = breaks[idx + 1];
          const colorIdx = Math.min(idx, palette_colors.length - 1);
          const isVisible = visible[idx];
          const rangeLabel = `${formatNum(val)} — ${formatNum(nextVal)}`;
          return (
            <div
              key={idx}
              role="button"
              tabIndex={0}
              aria-label={rangeLabel}
              className={`flex items-center justify-between group transition-all cursor-pointer hover:bg-muted/30 p-1 rounded-md ${!isVisible ? 'opacity-50' : ''}`}
              onClick={() => toggle(idx)}
              onKeyDown={(e) => { if (e.key === 'Enter') toggle(idx); }}
            >
              <div className="flex items-center gap-3">
                <div
                  className="w-3.5 h-3.5 rounded-sm shadow-sm ring-1 ring-black/10 group-hover:scale-110 transition-transform"
                  style={{ backgroundColor: palette_colors[colorIdx] }}
                />
                <span className="text-[15px] font-medium text-muted-foreground group-hover:text-foreground transition-colors">
                  {rangeLabel}
                </span>
              </div>
              <div className="flex items-center">
                {isVisible
                  ? <Eye className="h-3 w-3 text-primary/70" />
                  : <EyeOff className="h-3 w-3 text-muted-foreground/50" />}
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-4 pt-2 border-t border-border/40 text-[15px] text-muted-foreground/60 italic text-center">
        数据驱动专题渲染
      </div>
    </div>
  );
}
