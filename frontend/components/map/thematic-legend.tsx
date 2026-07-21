'use client';

import type { LegendSpec } from '@/lib/map-kit/types';
import { GraduatedLegend } from './legends/graduated-legend';
import { ContinuousLegend } from './legends/continuous-legend';
import { CategoricalLegend } from './legends/categorical-legend';
import { DivergentLegend } from './legends/divergent-legend';


import { devOnly } from "@/lib/utils/logger";
interface Props {
  spec: LegendSpec | null | undefined;
  onFilterChange?: (visibleBreaks: number[][]) => void;
}

export function ThematicLegend({ spec, onFilterChange }: Props) {
  if (!spec) return null;
  switch (spec.type) {
    case 'graduated':
      return <GraduatedLegend spec={spec} onFilterChange={onFilterChange} />;
    case 'continuous':
      return <ContinuousLegend spec={spec} />;
    case 'categorical':
      return <CategoricalLegend spec={spec} />;
    case 'divergent':
      return <DivergentLegend spec={spec} />;
    default: {
      const _exhaustive: never = spec;
      void _exhaustive;
      devOnly.warn('[ThematicLegend] unknown legend_spec type', spec);
      return null;
    }
  }
}
