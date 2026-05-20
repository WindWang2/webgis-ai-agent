'use client';

import type { DivergentLegendSpec, ContinuousLegendSpec } from '@/lib/map-kit/types';
import { ContinuousLegend } from './continuous-legend';

interface Props {
  spec: DivergentLegendSpec;
}

export function DivergentLegend({ spec }: Props) {
  // Stub: render divergent as continuous until hotspot z-score tool is added.
  const asContinuous: ContinuousLegendSpec = {
    type: 'continuous',
    field: spec.field,
    min: spec.min,
    max: spec.max,
    palette: spec.palette,
    palette_colors: spec.palette_colors,
  };
  return <ContinuousLegend spec={asContinuous} />;
}
