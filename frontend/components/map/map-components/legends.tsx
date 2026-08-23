'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass, stackedBottomStyle } from './helpers';
import type { RendererContext } from './types';
import type { LegendSpec } from '@/lib/map-kit/types';
import { formatLegendValue } from '../legends/legend-card';

function legendForComponent(component: MapSpecComponent, spec: RendererContext['spec']): LegendSpec | undefined {
  const layerId = (component as unknown as { options?: Record<string, unknown> }).options?.['layerId'];
  if (typeof layerId === 'string' && layerId && spec) {
    const layer = spec.layers.find((l) => l.id === layerId) as unknown as { legend_spec?: LegendSpec } | undefined;
    if (layer?.legend_spec) return layer.legend_spec;
  }
  const wanted: Record<string, string[]> = { legend: ['graduated'], categorical_legend: ['categorical'] };
  const types = wanted[component.type] ?? [];
  const found = spec?.layers.find((l) => {
    const ls = (l as unknown as { legend_spec?: { type?: string } }).legend_spec;
    return ls != null && (types.length === 0 || types.includes(String(ls.type ?? '')));
  }) as unknown as { legend_spec?: LegendSpec } | undefined;
  return found?.legend_spec;
}

function legendEntries(legend: LegendSpec | undefined): { color: string; label: string }[] {
  if (!legend) return [];
  const legacy = (legend as unknown as { entries?: unknown }).entries;
  if (Array.isArray(legacy)) return legacy as { color: string; label: string }[];
  if (legend.type === 'graduated') {
    const breaks = legend.breaks ?? [];
    const colors = (legend as unknown as { palette_colors?: string[] }).palette_colors ?? [];
    const labels = (legend as unknown as { labels?: unknown[] }).labels;
    const n = Math.min(breaks.length - 1, colors.length);
    if (n < 1) return [];
    return Array.from({ length: n }, (_, i) => ({
      color: colors[i],
      label: labels && labels[i] != null && String(labels[i]).trim() !== '' ? String(labels[i]) : `${formatLegendValue(breaks[i])} – ${formatLegendValue(breaks[i + 1])}`,
    }));
  }
  if (legend.type === 'categorical') {
    return ((legend as unknown as { categories?: { color: string; label?: string; key?: string }[] }).categories ?? [])
      .filter((c) => c != null && typeof c.color === 'string' && c.color)
      .map((c) => ({ color: c.color, label: c.label != null && String(c.label).trim() !== '' ? String(c.label) : String(c.key ?? '') }));
  }
  return [];
}

function LegendRenderer(component: MapSpecComponent, ctx: RendererContext) {
  const legend = legendForComponent(component, ctx.spec);
  if (!legend || legend.type !== 'graduated') return null;
  const entries = legendEntries(legend);
  if (!entries.length) return null;
  return (
    <div data-testid="spec-chrome-legend" style={stackedBottomStyle(component, ctx.bottomSlotIndexes)} className={`map-chrome absolute z-30 rounded-chrome px-2 py-1.5 ${positionClass(component)}`} aria-label="分级图例">
      {(legend as unknown as { title?: string }).title && <div className="text-micro font-medium text-map-chrome-ink">{(legend as unknown as { title: string }).title}</div>}
      <div className="mt-1 flex flex-col gap-1">
        {entries.slice(0, 8).map((e, j) => (
          <div key={j} className="flex items-center gap-1.5">
            <span aria-hidden className="h-2.5 w-4 rounded-sm" style={{ background: e.color }} />
            <span className="text-micro tabular-nums text-map-chrome-ink-muted">{e.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function CategoricalLegendRenderer(component: MapSpecComponent, ctx: RendererContext) {
  const legend = legendForComponent(component, ctx.spec);
  if (!legend || legend.type !== 'categorical') return null;
  const entries = legendEntries(legend);
  if (!entries.length) return null;
  return (
    <div data-testid="spec-chrome-categorical-legend" style={stackedBottomStyle(component, ctx.bottomSlotIndexes)} className={`map-chrome absolute z-30 rounded-chrome px-2 py-1.5 ${positionClass(component)}`} aria-label="分类图例">
      {(legend as unknown as { title?: string }).title && <div className="text-micro font-medium text-map-chrome-ink">{(legend as unknown as { title: string }).title}</div>}
      <div className="mt-1 flex flex-col gap-1">
        {entries.slice(0, 8).map((e, j) => (
          <div key={j} className="flex items-center gap-1.5">
            <span aria-hidden className="h-2.5 w-4 rounded-sm" style={{ background: e.color }} />
            <span className="text-micro text-map-chrome-ink-muted">{e.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

registerComponentRenderer('legend', LegendRenderer);
registerComponentRenderer('categorical_legend', CategoricalLegendRenderer);
