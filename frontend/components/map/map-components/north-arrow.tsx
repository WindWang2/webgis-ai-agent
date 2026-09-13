'use client';
import React from 'react';
import { Compass, Navigation2, Rose } from 'lucide-react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass, resolveVariant } from './helpers';
import type { RendererContext } from './types';
import { useT } from '@/lib/i18n/useT';
import { bboxCenter, magneticDeclinationAt } from '@/lib/layout/magnetic-declination';

// D7：arrow_simple —— 简单箭头字形（实心北向箭头 + 尾杆），随容器
// rotate(-bearing) 一起旋转（与其它 glyph 同一方位角语义）。
function SimpleArrowGlyph() {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="h-icon-md w-icon-md text-map-chrome-ink">
      <path d="M12 2.5 L16.5 13.5 L12 11 L7.5 13.5 Z" fill="currentColor" stroke="currentColor" strokeWidth="1" strokeLinejoin="round" />
      <line x1="12" y1="11" x2="12" y2="21.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function Glyph({ variant }: { variant: string }) {
  if (variant === 'compass_needle') return <Navigation2 aria-hidden className="h-icon-md w-icon-md text-map-chrome-ink" />;
  if (variant === 'compass_rose') return <Rose aria-hidden className="h-icon-md w-icon-md text-map-chrome-ink" />;
  if (variant === 'arrow_simple') return <SimpleArrowGlyph />;
  return <Compass aria-hidden className="h-icon-md w-icon-md text-map-chrome-ink" />;
}

// 注册表以普通函数调用 renderer（renderComponent → renderer(component, ctx)），
// hooks 必须住在真正的组件里（methodology-note 同款拆分）。
function NorthArrowView({ component, ctx }: { component: MapSpecComponent; ctx: RendererContext }) {
  const t = useT();
  // V3：统一走 resolveVariant（options.variant > 目录 variant 字段 >
  // 缺省）—— 组件变体的目录通道不再被绕过。
  const variant = resolveVariant(component, 'compass_minimal_black');
  // V3（ADR-0101 D3）：monochrome —— 灰度渲染（黑白出版/打印友好），
  // glyph 仍是 compass（单色语义），仅色彩通道去饱和。
  const mono = variant === 'monochrome';
  // AC-07（ADR-0156 P4）：真北/磁北偏角注记 —— bbox 中心偶极子近似
  // （恒 approximate 标记）；options.showDeclination === false 可关。
  const options = (component.options ?? {}) as Record<string, unknown>;
  const showDeclination = options['showDeclination'] !== false && !!ctx.bounds;
  const declination = showDeclination && ctx.bounds
    ? magneticDeclinationAt(
        bboxCenter(ctx.bounds).lat,
        bboxCenter(ctx.bounds).lng,
      )
    : undefined;
  return (
    <div data-testid="spec-chrome-north-arrow" data-variant={variant} className={`map-chrome absolute z-30 flex h-control-lg w-control-lg flex-col items-center justify-center gap-px rounded-chrome ${mono ? 'grayscale opacity-80' : ''} ${positionClass(component)}`} style={{ transform: `rotate(${-ctx.bearing}deg)` }} aria-label={`指北针（${variant}），当前方位角 ${Math.round(ctx.bearing)}°${declination ? `，磁偏角 ${declination.label}` : ''}`}>
      <Glyph variant={variant} />
      <span aria-hidden className="text-micro font-semibold leading-none text-map-chrome-ink-muted">N</span>
      {declination && (
        <span
          data-testid="spec-chrome-north-declination"
          className="text-micro leading-none tabular-nums text-map-chrome-ink-muted"
          title={t('map.chrome.declinationHint')}
        >
          {declination.label}
        </span>
      )}
    </div>
  );
}

function NorthArrowRenderer(component: MapSpecComponent, ctx: RendererContext) {
  return <NorthArrowView component={component} ctx={ctx} />;
}

registerComponentRenderer('north_arrow', NorthArrowRenderer);
