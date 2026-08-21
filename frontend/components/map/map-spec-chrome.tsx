'use client';

/**
 * MapSpecChrome —— MapSpec `layout.components` 的 live 渲染面。
 *
 * GIS Harness（app/services/gis_harness）把 CartographyComponent 写进
 * MapSpec layout.components；本组件按 enabled/position 渲染可替换制图
 * 组件（标题/副题/指北针/比例尺/署名/色条/图例）。MapSpec 是唯一
 * desired cartographic state —— live 渲染与 export 读同一份组件描述。
 *
 * 兼容语义：无 components 的旧 MapSpec 不渲染本组件（HUD 既有 chrome
 * 照旧）；有组件时按组件位置渲染，组件级 enabled=false 不显示。
 */
import React from 'react';
import { Compass, Navigation2, Rose } from 'lucide-react';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';
import type { LegendSpec } from '@/lib/map-kit/types';

// 与 map-decorations.tsx 同源的度量换算（单一算法，复制以保持模块独立）
const EARTH_CIRCUMFERENCE = 40_075_016.686;

function computeScale(zoom: number, lat: number): { meters: number; pixels: number } {
  const metersPerPixel =
    (EARTH_CIRCUMFERENCE * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom + 8);
  const targetMeters = metersPerPixel * 100;
  const candidates = [50, 100, 200, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 50_000, 100_000];
  let best = candidates[0];
  for (const c of candidates) {
    if (c <= targetMeters) best = c;
  }
  return { meters: best, pixels: best / metersPerPixel };
}

function formatMeters(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(m % 1000 === 0 ? 0 : 1)} km` : `${m} m`;
}

const POSITION_CLASS: Record<string, string> = {
  'top-left': 'top-3 left-3',
  'top-center': 'top-3 left-1/2 -translate-x-1/2',
  'top-right': 'top-3 right-3',
  'bottom-left': 'bottom-3 left-3',
  'bottom-center': 'bottom-3 left-1/2 -translate-x-1/2',
  'bottom-right': 'bottom-3 right-3',
  none: 'hidden',
};

function positionClass(component: MapSpecComponent, fallback: string): string {
  return POSITION_CLASS[component.position ?? 'none'] ?? fallback;
}

function NorthArrowGlyph({ variant }: { variant: string }) {
  // 组件变体（「换一个指南针」→ options.variant）：同一组件槽的可替换形态
  if (variant === 'compass_needle') {
    return <Navigation2 aria-hidden className="h-icon-md w-icon-md text-map-chrome-ink" />;
  }
  if (variant === 'compass_rose') {
    return <Rose aria-hidden className="h-icon-md w-icon-md text-map-chrome-ink" />;
  }
  return <Compass aria-hidden className="h-icon-md w-icon-md text-map-chrome-ink" />;
}

interface ChromeProps {
  components: MapSpecComponent[];
  zoom: number;
  centerLat: number;
  bearing: number;
  /** 色条/图例组件引用图层时，从 committed spec 的图层取 legend_spec。 */
  spec: MapSpec | null;
}

function legendForComponent(component: MapSpecComponent, spec: MapSpec | null): LegendSpec | undefined {
  const layerId = component.options?.['layerId'];
  if (typeof layerId === 'string' && layerId && spec) {
    const layer = spec.layers.find((l) => l.id === layerId) as
      | (typeof spec.layers[number] & { legend_spec?: LegendSpec })
      | undefined;
    if (layer?.legend_spec) return layer.legend_spec;
  }
  // 未指定图层 → 取第一个带 legend_spec 的可见图层
  const withLegend = spec?.layers.find(
    (l) => (l as { legend_spec?: LegendSpec }).legend_spec,
  ) as ({ legend_spec?: LegendSpec } & typeof spec.layers[number]) | undefined;
  return withLegend?.legend_spec;
}

export const MapSpecChrome = React.memo(function MapSpecChrome({
  components,
  zoom,
  centerLat,
  bearing,
  spec,
}: ChromeProps) {
  const enabled = components.filter((c) => c.enabled !== false);
  if (!enabled.length) return null;

  const { meters, pixels } = computeScale(zoom, centerLat);

  const byType = (type: string) => enabled.filter((c) => c.type === type);

  return (
    <>
      {byType('title').map((c) => {
        const text = typeof c.options?.['text'] === 'string' ? c.options['text'] : '';
        if (!text) return null;
        return (
          <div
            key={c.id}
            data-testid="spec-chrome-title"
            className={`map-chrome absolute z-30 max-w-[min(46ch,50%)] truncate px-3 py-1 text-title font-semibold ${positionClass(c, POSITION_CLASS['top-center'])}`}
          >
            {text}
          </div>
        );
      })}

      {byType('subtitle').map((c) => {
        const text = typeof c.options?.['text'] === 'string' ? c.options['text'] : '';
        if (!text) return null;
        return (
          <div
            key={c.id}
            data-testid="spec-chrome-subtitle"
            className={`map-chrome absolute z-30 max-w-[min(46ch,50%)] truncate px-3 text-caption text-map-chrome-ink-muted ${positionClass(c, POSITION_CLASS['top-center'])}`}
            style={{ top: 'calc(0.75rem + 1.75rem)' }}
          >
            {text}
          </div>
        );
      })}

      {byType('north_arrow').map((c) => {
        const variant = typeof c.options?.['variant'] === 'string' ? c.options['variant'] : 'compass_minimal_black';
        return (
          <div
            key={c.id}
            data-testid="spec-chrome-north-arrow"
            className={`map-chrome absolute z-30 flex h-control-lg w-control-lg flex-col items-center justify-center gap-px rounded-chrome ${positionClass(c, POSITION_CLASS['top-right'])}`}
            style={{ transform: `rotate(${-bearing}deg)` }}
            aria-label={`指北针（${variant}），当前方位角 ${Math.round(bearing)}°`}
          >
            <NorthArrowGlyph variant={variant} />
            <span aria-hidden className="text-micro font-semibold leading-none text-map-chrome-ink-muted">N</span>
          </div>
        );
      })}

      {byType('scale_bar').map((c) => (
        <div
          key={c.id}
          data-testid="spec-chrome-scale-bar"
          className={`map-chrome absolute z-30 flex items-center gap-2 px-2 py-1 text-caption font-medium tabular-nums ${positionClass(c, POSITION_CLASS['bottom-right'])}`}
          aria-label={`比例尺 ${formatMeters(meters)}`}
        >
          <div
            aria-hidden
            className="border-b-2 border-l-2 border-r-2 border-map-chrome-ink"
            style={{ width: `${Math.round(pixels)}px`, height: '5px' }}
          />
          <span className="text-map-chrome-ink">{formatMeters(meters)}</span>
        </div>
      ))}

      {byType('attribution').map((c) => {
        const text = typeof c.options?.['text'] === 'string' ? c.options['text'] : '';
        if (!text) return null;
        return (
          <div
            key={c.id}
            data-testid="spec-chrome-attribution"
            className={`map-chrome absolute z-30 px-2 py-0.5 text-micro text-map-chrome-ink-muted ${positionClass(c, POSITION_CLASS['bottom-left'])}`}
          >
            {text}
          </div>
        );
      })}

      {byType('continuous_colorbar').map((c) => {
        const legend = legendForComponent(c, spec);
        const colors =
          legend && (legend.type === 'continuous' || legend.type === 'divergent')
            ? legend.palette_colors
            : undefined;
        if (!colors || colors.length < 2) return null;
        const vertical = c.options?.['orientation'] === 'vertical';
        const gradient = `linear-gradient(to ${vertical ? 'bottom' : 'right'}, ${colors.join(', ')})`;
        return (
          <div
            key={c.id}
            data-testid="spec-chrome-colorbar"
            className={`map-chrome absolute z-30 rounded-chrome px-2 py-1.5 ${positionClass(c, POSITION_CLASS['bottom-right'])}`}
            aria-label="连续密度色条"
          >
            {legend.min !== undefined && legend.max !== undefined && (
              <div className={`flex ${vertical ? 'flex-row gap-1' : 'flex-col gap-0.5'} text-micro tabular-nums text-map-chrome-ink`}>
                {vertical && <span>{Number(legend.max).toFixed(1)}</span>}
                <div
                  aria-hidden
                  className={vertical ? 'w-2.5 rounded-sm' : 'h-2.5 w-36 rounded-sm'}
                  style={{ background: gradient }}
                />
                <div className={`flex ${vertical ? 'flex-col justify-between' : 'justify-between'} w-full text-map-chrome-ink-muted`}>
                  <span>{Number(legend.min).toFixed(1)}</span>
                  {!vertical && <span>{Number(legend.max).toFixed(1)}</span>}
                </div>
              </div>
            ) || (
              <div aria-hidden className="h-2.5 w-36 rounded-sm" style={{ background: gradient }} />
            )}
          </div>
        );
      })}

      {byType('legend').map((c) => {
        const legend = legendForComponent(c, spec);
        if (!legend || legend.type !== 'graduated') return null;
        return (
          <div
            key={c.id}
            data-testid="spec-chrome-legend"
            className={`map-chrome absolute z-30 rounded-chrome px-2 py-1.5 ${positionClass(c, POSITION_CLASS['bottom-left'])}`}
            aria-label="分级图例"
          >
            {legend.title && (
              <div className="text-micro font-medium text-map-chrome-ink">{legend.title}</div>
            )}
            <div className="mt-1 flex flex-col gap-1">
              {legend.entries.slice(0, 8).map((entry, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <span aria-hidden className="h-2.5 w-4 rounded-sm" style={{ background: entry.color }} />
                  <span className="text-micro tabular-nums text-map-chrome-ink-muted">{entry.label}</span>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {byType('categorical_legend').map((c) => {
        const legend = legendForComponent(c, spec);
        if (!legend || legend.type !== 'categorical') return null;
        return (
          <div
            key={c.id}
            data-testid="spec-chrome-categorical-legend"
            className={`map-chrome absolute z-30 rounded-chrome px-2 py-1.5 ${positionClass(c, POSITION_CLASS['bottom-left'])}`}
            aria-label="分类图例"
          >
            {legend.title && (
              <div className="text-micro font-medium text-map-chrome-ink">{legend.title}</div>
            )}
            <div className="mt-1 flex flex-col gap-1">
              {legend.entries.slice(0, 8).map((entry, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <span aria-hidden className="h-2.5 w-4 rounded-sm" style={{ background: entry.color }} />
                  <span className="text-micro text-map-chrome-ink-muted">{entry.label}</span>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </>
  );
});
