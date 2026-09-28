'use client';

import type { DivergentLegendSpec, ContinuousLegendSpec } from '@/lib/map-kit/types';
import { useT } from '@/lib/i18n/useT';
import { ContinuousLegend } from './continuous-legend';

interface Props {
  spec: DivergentLegendSpec;
}

export function DivergentLegend({ spec }: Props) {
  const t = useT();
  // Stub: render divergent as continuous until hotspot z-score tool is added.
  const asContinuous: ContinuousLegendSpec = {
    type: 'continuous',
    field: spec.field,
    min: spec.min,
    max: spec.max,
    palette: spec.palette,
    palette_colors: spec.palette_colors,
  };
  // Pass the divergent label through: sharing the continuous renderer is fine,
  // silently inheriting its 「连续密度渲染」 footer was mislabelling the map.
  return <ContinuousLegend spec={asContinuous} kind={t('map.legends.divergent')} />;
}
