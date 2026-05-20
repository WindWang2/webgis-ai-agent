'use client';

import { Info } from 'lucide-react';
import type { CategoricalLegendSpec } from '@/lib/map-kit/types';

interface Props {
  spec: CategoricalLegendSpec;
}

export function CategoricalLegend({ spec }: Props) {
  const { field, categories } = spec;
  return (
    <div className="bg-card/90 backdrop-blur-md border border-border p-4 rounded-xl shadow-2xl min-w-[200px] animate-in slide-in-from-right-4 duration-500">
      <div className="flex items-center gap-2 mb-3 border-b border-border pb-2">
        <div className="p-1 bg-primary/10 rounded-md">
          <Info className="h-3.5 w-3.5 text-primary" />
        </div>
        <div className="flex flex-col">
          <span className="text-[10px] uppercase font-bold tracking-widest text-muted-foreground/80">图例说明</span>
          <span className="text-xs font-semibold text-foreground truncate max-w-[140px]" title={field}>
            字段: {field}
          </span>
        </div>
      </div>
      <div className="space-y-2">
        {categories.map((c) => (
          <div key={c.key} className="flex items-center gap-3 p-1">
            <div
              data-testid="cat-swatch"
              className="w-3.5 h-3.5 rounded-sm shadow-sm ring-1 ring-black/10"
              style={{ backgroundColor: c.color }}
            />
            <span className="text-[11px] font-medium text-muted-foreground">{c.label}</span>
          </div>
        ))}
      </div>
      <div className="mt-4 pt-2 border-t border-border/40 text-[9px] text-muted-foreground/60 italic text-center">
        分类专题
      </div>
    </div>
  );
}
