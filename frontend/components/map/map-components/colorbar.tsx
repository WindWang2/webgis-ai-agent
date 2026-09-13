'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass, resolveVariant, stackedBottomStyle } from './helpers';
import type { RendererContext } from './types';
import type { LegendSpec } from '@/lib/map-kit/types';
import { formatLegendValue } from '@/components/map/legends/legend-card';
import {
  legendNodataLabel,
  legendOutOfRangeLabel,
} from '@/lib/layout/legend-labels';

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
  // D7：slim = 更细色条；compact = 紧凑内边距；report = 卡片加宽 + 刻度强调
  // V3（ADR-0101 D3）：scientific = 中间刻度行（两端 + 3 个内插读数）；
  // stepped = 离散色阶块（palette_colors 分箱，而非连续渐变）。
  const variant = resolveVariant(component, '');
  // 方向载体：options.orientation 优先；variant horizontal/vertical 与
  // 目录词表一致（仅在缺 orientation 时作为回退，不再是无操作值）。
  const orientationOpt = (component as unknown as { options?: Record<string, unknown> }).options?.['orientation'];
  const vertical = orientationOpt === 'vertical' || (orientationOpt === undefined && variant === 'vertical');
  const slim = variant === 'slim';
  const scientific = variant === 'scientific';
  const stepped = variant === 'stepped';
  const padClass = variant === 'compact' ? 'px-1.5 py-1' : variant === 'report' ? 'px-3 py-2' : 'px-2 py-1.5';
  const barClass = vertical
    ? `${slim ? 'w-1.5' : 'w-2.5'} rounded-sm`
    : `${slim ? 'h-1.5' : 'h-2.5'} w-36 rounded-sm`;
  const gradient = `linear-gradient(to ${vertical ? 'bottom' : 'right'}, ${colors.join(', ')})`;
  const range = legend as unknown as { min?: number; max?: number; unit?: string };
  const hasRange = range.min !== undefined && range.max !== undefined;
  // scientific 中间刻度：3 个内插读数（25/50/75%），与两端同一 formatter。
  const ticks = scientific && hasRange
    ? [0.25, 0.5, 0.75].map((t) => formatLegendValue(Number(range.min) + (Number(range.max) - Number(range.min)) * t))
    : [];
  const ariaLabel = `密度色条${scientific ? '（科学刻度）' : ''}${stepped ? '（分级色阶）' : ''}`;
  // AC-07（ADR-0156 P7）：nodata 色块与 out_of_range 标签 —— 与图例卡
  // 同一 v2 消费面（legend-labels 单源），连续型与分类型表达统一。
  const nodata = (legend as unknown as { nodata?: { color?: string; label?: string } }).nodata;
  const nodataText = legendNodataLabel(legend);
  const outOfRangeText = legendOutOfRangeLabel(legend);
  return (
    <div data-testid="spec-chrome-colorbar" data-variant={variant} style={stackedBottomStyle(component, ctx.bottomSlotIndexes)} className={`map-chrome absolute z-30 rounded-chrome ${padClass} ${positionClass(component)}`} aria-label={ariaLabel}>
      {hasRange ? (
        <div className={`flex ${vertical ? 'flex-row gap-1' : 'flex-col gap-0.5'} text-micro tabular-nums text-map-chrome-ink`}>
          {stepped ? (
            <div aria-hidden className={`flex ${vertical ? 'flex-col' : ''} overflow-hidden rounded-sm`} data-stepped="true">
              {colors.map((c, i) => (
                <span key={`${c}#${i}`} className={vertical ? 'h-3 w-2.5' : 'h-2.5 w-9'} style={{ background: c }} />
              ))}
            </div>
          ) : (
            <div aria-hidden className={barClass} style={{ background: gradient, backgroundImage: gradient }} data-gradient={gradient} />
          )}
          {ticks.length ? (
            <div aria-hidden className={`flex ${vertical ? 'flex-1 flex-col-reverse justify-between' : 'w-full flex-row justify-between'} text-map-chrome-ink-muted`}>
              {ticks.map((t, i) => (
                <span key={`${t}#${i}`}>{t}</span>
              ))}
            </div>
          ) : null}
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
        <div aria-hidden className={barClass} style={{ background: gradient, backgroundImage: gradient }} data-gradient={gradient} />
      )}
      {nodata?.color && (
        <div data-testid="spec-chrome-colorbar-nodata" className="mt-1 flex items-center gap-1 text-micro text-map-chrome-ink-muted">
          <span aria-hidden className="h-2.5 w-4 rounded-sm border border-map-chrome-border" style={{ background: nodata.color }} />
          <span>{nodataText}</span>
        </div>
      )}
      {outOfRangeText && (
        <div data-testid="spec-chrome-colorbar-out-of-range" className="mt-0.5 text-micro text-map-chrome-ink-muted">
          {outOfRangeText}
        </div>
      )}
    </div>
  );
}

registerComponentRenderer('continuous_colorbar', ColorbarRenderer);
