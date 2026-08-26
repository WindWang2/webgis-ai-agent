'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass, resolveVariant, stackedBottomStyle } from './helpers';
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

// D7：legend 族 variant —— compact（紧凑内边距/行距）| academic（缺省现状）
// | report（卡片 + 标题强调条）。未知 variant 确定性回退 academic。
function legendVariantClasses(variant: string): { root: string; title: string } {
  if (variant === 'compact') {
    return { root: 'px-1.5 py-1', title: 'text-micro font-medium' };
  }
  if (variant === 'report') {
    return { root: 'px-3 py-2', title: 'border-b border-map-chrome-border pb-1 text-caption font-semibold' };
  }
  return { root: 'px-2 py-1.5', title: 'text-micro font-medium' };
}

function LegendRenderer(component: MapSpecComponent, ctx: RendererContext) {
  const legend = legendForComponent(component, ctx.spec);
  if (!legend || legend.type !== 'graduated') return null;
  const entries = legendEntries(legend);
  if (!entries.length) return null;
  const variant = resolveVariant(component, 'academic');
  const classes = legendVariantClasses(variant);
  const compact = variant === 'compact';
  return (
    <div data-testid="spec-chrome-legend" data-variant={variant} style={stackedBottomStyle(component, ctx.bottomSlotIndexes)} className={`map-chrome absolute z-30 rounded-chrome ${classes.root} ${positionClass(component)}`} aria-label="分级图例">
      {(legend as unknown as { title?: string }).title && <div className={`text-map-chrome-ink ${classes.title}`}>{(legend as unknown as { title: string }).title}</div>}
      <div className={`flex flex-col ${compact ? 'mt-0.5 gap-0.5' : 'mt-1 gap-1'}`}>
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
  const variant = resolveVariant(component, 'academic');
  const classes = legendVariantClasses(variant);
  const compact = variant === 'compact';
  return (
    <div data-testid="spec-chrome-categorical-legend" data-variant={variant} style={stackedBottomStyle(component, ctx.bottomSlotIndexes)} className={`map-chrome absolute z-30 rounded-chrome ${classes.root} ${positionClass(component)}`} aria-label="分类图例">
      {(legend as unknown as { title?: string }).title && <div className={`text-map-chrome-ink ${classes.title}`}>{(legend as unknown as { title: string }).title}</div>}
      <div className={`flex flex-col ${compact ? 'mt-0.5 gap-0.5' : 'mt-1 gap-1'}`}>
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
