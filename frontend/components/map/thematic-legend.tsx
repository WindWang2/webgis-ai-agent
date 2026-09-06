'use client';

import React from 'react';
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

export const ThematicLegend = React.memo(function ThematicLegend({ spec, onFilterChange }: Props) {
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
    case 'bivariate':
      // V4：双变量色阵 —— map-components/legends.tsx 的 BivariateMatrix
      // 是 chrome 组件渲染器；此处 chat 结果卡路径用同一 3×3 网格形态。
      return (
        <div className="flex flex-col gap-1">
          <div className="grid gap-px" style={{ gridTemplateColumns: `repeat(${spec.n}, 16px)` }}>
            {spec.colors.map((c, i) => (
              <span key={i} className="h-4 w-4" style={{ background: c }} />
            ))}
          </div>
          <div className="text-xs text-muted">→{spec.label_a} / ↑{spec.label_b}</div>
        </div>
      );
    default: {
      const _exhaustive: never = spec;
      void _exhaustive;
      devOnly.warn('[ThematicLegend] unknown legend_spec type', spec);
      return null;
    }
  }
});
