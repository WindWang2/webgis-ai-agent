'use client';

import type { ContinuousLegendSpec } from '@/lib/map-kit/types';
import { LegendCard, formatLegendValue } from './legend-card';

interface Props {
  spec: ContinuousLegendSpec;
  /** Renderer description for the footer. Divergent passes its own. */
  kind?: string;
}

export function ContinuousLegend({ spec, kind = '连续密度渲染' }: Props) {
  const { field, min, max, palette_colors } = spec;
  const gradient = `linear-gradient(to right, ${palette_colors.join(', ')})`;
  return (
    <LegendCard field={field} kind={kind}>
      <div className="space-y-1">
        <div
          aria-hidden
          className="h-2.5 rounded-xs ring-1 ring-inset ring-map-chrome-border"
          style={{ background: gradient }}
        />
        <div className="flex justify-between text-meta tabular-nums text-map-chrome-ink">
          <span>{formatLegendValue(min)}</span>
          <span>{formatLegendValue(max)}</span>
        </div>
      </div>
    </LegendCard>
  );
}
