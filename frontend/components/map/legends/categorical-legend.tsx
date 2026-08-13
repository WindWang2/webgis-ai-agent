'use client';

import type { CategoricalLegendSpec } from '@/lib/map-kit/types';
import { LegendCard } from './legend-card';

interface Props {
  spec: CategoricalLegendSpec;
}

export function CategoricalLegend({ spec }: Props) {
  const { field, categories } = spec;
  return (
    <LegendCard field={field} kind="分类专题">
      <div className="space-y-1">
        {categories.map((c) => (
          <div key={c.key} className="flex items-center gap-2">
            <div
              data-testid="cat-swatch"
              className="h-icon-sm w-icon-sm shrink-0 rounded-xs ring-1 ring-inset ring-map-chrome-border"
              style={{ backgroundColor: c.color }}
            />
            <span className="truncate text-meta text-map-chrome-ink" title={c.label}>
              {c.label}
            </span>
          </div>
        ))}
      </div>
    </LegendCard>
  );
}
