'use client';

import type { CategoricalLegendSpec } from '@/lib/map-kit/types';
import { useT } from '@/lib/i18n/useT';
import { LegendCard } from './legend-card';

interface Props {
  spec: CategoricalLegendSpec;
}

export function CategoricalLegend({ spec }: Props) {
  const t = useT();
  const { field, categories } = spec;
  return (
    <LegendCard field={field} kind={t('map.legends.categorical')}>
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
        {spec.default && (
          <div className="flex items-center gap-2 opacity-80" data-testid="cat-other-swatch">
            <div
              className="h-icon-sm w-icon-sm shrink-0 rounded-xs ring-1 ring-dashed ring-inset ring-map-chrome-border"
              style={{ backgroundColor: spec.default.color }}
            />
            <span className="truncate text-meta text-map-chrome-ink italic">
              {spec.default.label || t('map.legends.other')}
            </span>
          </div>
        )}
      </div>
    </LegendCard>
  );
}
