'use client';

import { Info } from 'lucide-react';
import type { ContinuousLegendSpec } from '@/lib/map-kit/types';

interface Props {
  spec: ContinuousLegendSpec;
}

const fmt = (n: number) => {
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (Math.abs(n) >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return n.toFixed(1);
};

export function ContinuousLegend({ spec }: Props) {
  const { field, min, max, palette_colors } = spec;
  const gradient = `linear-gradient(to right, ${palette_colors.join(', ')})`;
  return (
    <div className="bg-card/90 backdrop-blur-md border border-border p-4 rounded-xl shadow-2xl min-w-[200px] animate-in slide-in-from-right-4 duration-500">
      <div className="flex items-center gap-2 mb-3 border-b border-border pb-2">
        <div className="p-1 bg-primary/10 rounded-md">
          <Info className="h-3.5 w-3.5 text-primary" />
        </div>
        <div className="flex flex-col">
          <span className="text-[14px] uppercase font-bold tracking-widest text-muted-foreground/80">图例说明</span>
          {field && (
            <span className="text-xs font-semibold text-foreground truncate max-w-[140px]" title={field}>
              字段: {field}
            </span>
          )}
        </div>
      </div>
      <div className="space-y-2">
        <div className="h-3 rounded-sm shadow-inner" style={{ background: gradient }} />
        <div className="flex justify-between text-[15px] text-muted-foreground">
          <span>{fmt(min)}</span>
          <span>{fmt(max)}</span>
        </div>
      </div>
      <div className="mt-4 pt-2 border-t border-border/40 text-[15px] text-muted-foreground/60 italic text-center">
        连续密度渲染
      </div>
    </div>
  );
}
