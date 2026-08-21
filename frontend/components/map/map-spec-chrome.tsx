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

// 各类型缺省位置（组件未声明 position 时的回退，与既有 HUD chrome 同位）
const DEFAULT_POSITION: Record<string, string> = {
  title: 'top-center',
  subtitle: 'top-center',
  north_arrow: 'top-right',
  scale_bar: 'bottom-right',
  attribution: 'bottom-left',
  continuous_colorbar: 'bottom-right',
  legend: 'bottom-left',
  categorical_legend: 'bottom-left',
};

// 底部组件必须让位状态栏/HUD（与 map-decorations 同一 CSS 变量约定：
// --map-chrome-bottom 随底部 HUD 展开），否则会叠在状态读数上。
const BOTTOM_OFFSET_STYLE: Record<string, React.CSSProperties> = {
  'bottom-left': { bottom: 'calc(var(--map-chrome-bottom, 10px) + 6px)' },
  'bottom-center': { bottom: 'calc(var(--map-chrome-bottom, 10px) + 6px)' },
  'bottom-right': { bottom: 'calc(var(--map-chrome-bottom, 10px) + 30px)' },
};

function resolvePosition(component: MapSpecComponent): string {
  return component.position ?? DEFAULT_POSITION[component.type] ?? 'none';
}

function positionClass(component: MapSpecComponent): string {
  const pos = resolvePosition(component);
  return POSITION_CLASS[pos] ?? POSITION_CLASS[DEFAULT_POSITION[component.type] ?? 'none'] ?? 'hidden';
}

function positionStyle(component: MapSpecComponent): React.CSSProperties | undefined {
  return BOTTOM_OFFSET_STYLE[resolvePosition(component)];
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
  // 未指定图层 → 取第一个带 legend_spec 的图层
  const layers = spec?.layers ?? [];
  const withLegend = layers.find(
    (l) => (l as { legend_spec?: LegendSpec }).legend_spec,
  ) as ({ legend_spec?: LegendSpec } & typeof layers[number]) | undefined;
  return withLegend?.legend_spec;
}

/** 连续图例的 min/max（类型收窄到 Continuous/Divergent 形态）。 */
function continuousRange(
  legend: LegendSpec | undefined,
): { min?: number; max?: number } {
  if (!legend) return {};
  const spec = legend as unknown as { min?: number; max?: number };
  return { min: spec.min, max: spec.max };
}

/** 图例条目守卫：畸形 legend_spec（缺 entries）不得炸掉整个地图面板。 */
function legendEntries(legend: LegendSpec | undefined): { color: string; label: string }[] {
  const entries = (legend as unknown as { entries?: unknown })?.entries;
  return Array.isArray(entries) ? (entries as { color: string; label: string }[]) : [];
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
  // 后端 lifecycle 拒绝重复 id；这里再以 index 兜底 key 唯一性
  const keyOf = (c: MapSpecComponent, i: number) => `${c.id}#${i}`;

  return (
    <>
      {byType('title').map((c, i) => {
        const text = typeof c.options?.['text'] === 'string' ? c.options['text'] : '';
        if (!text) return null;
        return (
          <div
            key={keyOf(c, i)}
            data-testid="spec-chrome-title"
            className={`map-chrome absolute z-30 max-w-[min(46ch,50%)] truncate px-3 py-1 text-title font-semibold ${positionClass(c)}`}
          >
            {text}
          </div>
        );
      })}

      {byType('subtitle').map((c, i) => {
        const text = typeof c.options?.['text'] === 'string' ? c.options['text'] : '';
        if (!text) return null;
        const pos = resolvePosition(c);
        // 副题只对顶部位置做下移偏移；底部位置直接贴位，避免 top+bottom 双锚拉伸
        const style =
          pos.startsWith('top') ? { top: 'calc(0.75rem + 1.75rem)' } : positionStyle(c);
        return (
          <div
            key={keyOf(c, i)}
            data-testid="spec-chrome-subtitle"
            style={style}
            className={`map-chrome absolute z-30 max-w-[min(46ch,50%)] truncate px-3 text-caption text-map-chrome-ink-muted ${positionClass(c)}`}
          >
            {text}
          </div>
        );
      })}

      {byType('north_arrow').map((c, i) => {
        const variant = typeof c.options?.['variant'] === 'string' ? c.options['variant'] : 'compass_minimal_black';
        return (
          <div
            key={keyOf(c, i)}
            data-testid="spec-chrome-north-arrow"
            className={`map-chrome absolute z-30 flex h-control-lg w-control-lg flex-col items-center justify-center gap-px rounded-chrome ${positionClass(c)}`}
            style={{ transform: `rotate(${-bearing}deg)` }}
            aria-label={`指北针（${variant}），当前方位角 ${Math.round(bearing)}°`}
          >
            <NorthArrowGlyph variant={variant} />
            <span aria-hidden className="text-micro font-semibold leading-none text-map-chrome-ink-muted">N</span>
          </div>
        );
      })}

      {byType('scale_bar').map((c, i) => (
        <div
          key={keyOf(c, i)}
          data-testid="spec-chrome-scale-bar"
          style={positionStyle(c)}
          className={`map-chrome absolute z-30 flex items-center gap-2 px-2 py-1 text-caption font-medium tabular-nums ${positionClass(c)}`}
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

      {byType('attribution').map((c, i) => {
        const text = typeof c.options?.['text'] === 'string' ? c.options['text'] : '';
        if (!text) return null;
        return (
          <div
            key={keyOf(c, i)}
            data-testid="spec-chrome-attribution"
            style={positionStyle(c)}
            className={`map-chrome absolute z-30 px-2 py-0.5 text-micro text-map-chrome-ink-muted ${positionClass(c)}`}
          >
            {text}
          </div>
        );
      })}

      {byType('continuous_colorbar').map((c, i) => {
        const legend = legendForComponent(c, spec);
        const colors =
          legend && (legend.type === 'continuous' || legend.type === 'divergent')
            ? legend.palette_colors
            : undefined;
        if (!colors || colors.length < 2) return null;
        const vertical = c.options?.['orientation'] === 'vertical';
        const gradient = `linear-gradient(to ${vertical ? 'bottom' : 'right'}, ${colors.join(', ')})`;
        const range = continuousRange(legend);
        const hasRange = range.min !== undefined && range.max !== undefined;
        return (
          <div
            key={keyOf(c, i)}
            data-testid="spec-chrome-colorbar"
            style={positionStyle(c)}
            className={`map-chrome absolute z-30 rounded-chrome px-2 py-1.5 ${positionClass(c)}`}
            aria-label="连续密度色条"
          >
            {hasRange ? (
              <div className={`flex ${vertical ? 'flex-row gap-1' : 'flex-col gap-0.5'} text-micro tabular-nums text-map-chrome-ink`}>
                <div
                  aria-hidden
                  className={vertical ? 'w-2.5 rounded-sm' : 'h-2.5 w-36 rounded-sm'}
                  style={{ background: gradient }}
                />
                <div className="flex w-full justify-between text-map-chrome-ink-muted">
                  <span>{Number(range.min).toFixed(1)}</span>
                  <span>{Number(range.max).toFixed(1)}</span>
                </div>
              </div>
            ) : (
              <div aria-hidden className="h-2.5 w-36 rounded-sm" style={{ background: gradient }} />
            )}
          </div>
        );
      })}

      {byType('legend').map((c, i) => {
        const legend = legendForComponent(c, spec);
        if (!legend || legend.type !== 'graduated') return null;
        const entries = legendEntries(legend);
        if (!entries.length) return null;
        return (
          <div
            key={keyOf(c, i)}
            data-testid="spec-chrome-legend"
            style={positionStyle(c)}
            className={`map-chrome absolute z-30 rounded-chrome px-2 py-1.5 ${positionClass(c)}`}
            aria-label="分级图例"
          >
            {legend.title && (
              <div className="text-micro font-medium text-map-chrome-ink">{legend.title}</div>
            )}
            <div className="mt-1 flex flex-col gap-1">
              {entries.slice(0, 8).map((entry, j) => (
                <div key={j} className="flex items-center gap-1.5">
                  <span aria-hidden className="h-2.5 w-4 rounded-sm" style={{ background: entry.color }} />
                  <span className="text-micro tabular-nums text-map-chrome-ink-muted">{entry.label}</span>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {byType('categorical_legend').map((c, i) => {
        const legend = legendForComponent(c, spec);
        if (!legend || legend.type !== 'categorical') return null;
        const entries = legendEntries(legend);
        if (!entries.length) return null;
        return (
          <div
            key={keyOf(c, i)}
            data-testid="spec-chrome-categorical-legend"
            style={positionStyle(c)}
            className={`map-chrome absolute z-30 rounded-chrome px-2 py-1.5 ${positionClass(c)}`}
            aria-label="分类图例"
          >
            {legend.title && (
              <div className="text-micro font-medium text-map-chrome-ink">{legend.title}</div>
            )}
            <div className="mt-1 flex flex-col gap-1">
              {entries.slice(0, 8).map((entry, j) => (
                <div key={j} className="flex items-center gap-1.5">
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
