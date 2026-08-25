'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass, stackedBottomStyle } from './helpers';
import type { RendererContext } from './types';
import type { LegendSpec } from '@/lib/map-kit/types';
import { formatLegendValue } from '@/components/map/legends/legend-card';

const LEGEND_TYPE_BY_COMPONENT: Record<string, string[]> = {
  continuous_colorbar: ['continuous', 'divergent'],
  legend: ['graduated'],
  categorical_legend: ['categorical'],
};

function legendForComponent(component: MapSpecComponent, spec: RendererContext['spec']): LegendSpec | undefined {
  const layerId = (component as unknown as { options?: Record<string, unknown> }).options?.['layerId'];
  if (typeof layerId === 'string' && layerId && spec) {
    const layer = spec.layers.find((l) => l.id === layerId) as unknown as { legend_spec?: LegendSpec } | undefined;
    if (layer?.legend_spec) return layer.legend_spec;
  }
  const wanted = LEGEND_TYPE_BY_COMPONENT[component.type] ?? [];
  const layers = spec?.layers ?? [];
  const withLegend = layers.find((l) => {
    const ls = (l as unknown as { legend_spec?: { type?: string } }).legend_spec;
    return ls != null && (wanted.length === 0 || wanted.includes(String(ls.type ?? '')));
  }) as unknown as { legend_spec?: LegendSpec } | undefined;
  return withLegend?.legend_spec;
}

function ColorbarRenderer(component: MapSpecComponent, ctx: RendererContext) {
  const legend = legendForComponent(component, ctx.spec);
  const colors = legend && (legend.type === 'continuous' || legend.type === 'divergent') ? (legend as unknown as { palette_colors: string[] }).palette_colors : undefined;
  if (!colors || colors.length < 2) return null;
  const vertical = (component as unknown as { options?: Record<string, unknown> }).options?.['orientation'] === 'vertical';
  const gradient = `linear-gradient(to ${vertical ? 'bottom' : 'right'}, ${colors.join(', ')})`;
  const range = legend as unknown as { min?: number; max?: number; unit?: string };
  const hasRange = range.min !== undefined && range.max !== undefined;
  return (
    <div data-testid="spec-chrome-colorbar" style={stackedBottomStyle(component, ctx.bottomSlotIndexes)} className={`map-chrome absolute z-30 rounded-chrome px-2 py-1.5 ${positionClass(component)}`} aria-label="连续密度色条">
      {hasRange ? (
        <div className={`flex ${vertical ? 'flex-row gap-1' : 'flex-col gap-0.5'} text-micro tabular-nums text-map-chrome-ink`}>
          <div aria-hidden className={vertical ? 'w-2.5 rounded-sm' : 'h-2.5 w-36 rounded-sm'} style={{ background: gradient, backgroundImage: gradient }} data-gradient={gradient} />
          <div className="flex w-full items-baseline justify-between gap-1 text-map-chrome-ink-muted">
            {/* #998：两端刻度走统一 formatLegendValue（千分位 / M-k 压缩 /
                非零不打印零），与图例读数一致——固定 toFixed(1) 会把
                0–0.004 的密度区间两端都印成 0.0，完全失真；unit（如
                人/km²）此前被丢弃。 */}
            <span>{formatLegendValue(Number(range.min))}</span>
            {range.unit ? (
              <span className="min-w-0 truncate text-map-chrome-ink-muted" title={range.unit}>
                {range.unit}
              </span>
            ) : null}
            <span>{formatLegendValue(Number(range.max))}</span>
          </div>
        </div>
      ) : (
        <div aria-hidden className="h-2.5 w-36 rounded-sm" style={{ background: gradient, backgroundImage: gradient }} data-gradient={gradient} />
      )}
    </div>
  );
}

registerComponentRenderer('continuous_colorbar', ColorbarRenderer);
