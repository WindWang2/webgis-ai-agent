'use client';

import { Palette, Target } from 'lucide-react';
import type { LegendSpec } from '@/lib/map-kit/types';

interface Props {
  result: { legend_spec?: LegendSpec; layer_meta?: { title?: string } } | null | undefined;
  layerId: string;
  onFocus?: (layerId: string) => void;
}

function summarize(spec: LegendSpec): string {
  switch (spec.type) {
    case 'graduated':
      return `${spec.field} · ${spec.breaks.length - 1} 分级`;
    case 'continuous':
      return `${spec.field ?? '密度'} · 连续色带`;
    case 'categorical':
      return `${spec.field} · ${spec.categories.length} 类`;
    case 'divergent':
      return `${spec.field ?? '指标'} · 发散色带`;
  }
}

function swatches(spec: LegendSpec): string[] {
  switch (spec.type) {
    case 'graduated':
    case 'continuous':
    case 'divergent':
      return spec.palette_colors;
    case 'categorical':
      return spec.categories.map((c) => c.color);
  }
}

export function CartographyResultCard({ result, layerId, onFocus }: Props) {
  const spec = result?.legend_spec;
  if (!spec) return null;
  const title = result?.layer_meta?.title ?? '专题图';
  const colors = swatches(spec);
  return (
    <div className="my-2 p-3 rounded-lg border border-border bg-card/70">
      <div className="flex items-center gap-2 mb-2">
        <Palette className="h-4 w-4 text-primary" />
        <span className="text-sm font-semibold text-foreground truncate">{title}</span>
      </div>
      <div className="flex items-center gap-1 mb-2">
        {colors.map((c, i) => (
          <div
            key={i}
            data-testid="card-swatch"
            className="w-5 h-3 rounded-sm ring-1 ring-black/10"
            style={{ backgroundColor: c }}
          />
        ))}
      </div>
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-muted-foreground">{summarize(spec)}</span>
        <button
          type="button"
          onClick={() => onFocus?.(layerId)}
          className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
        >
          <Target className="h-3 w-3" />
          高亮此图层
        </button>
      </div>
    </div>
  );
}
