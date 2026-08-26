'use client';
import React from 'react';
import { Compass, Navigation2, Rose } from 'lucide-react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass } from './helpers';
import type { RendererContext } from './types';

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

function NorthArrowRenderer(component: MapSpecComponent, ctx: RendererContext) {
  const variant = typeof (component as unknown as { options?: Record<string, unknown> }).options?.['variant'] === 'string'
    ? (component as unknown as { options: Record<string, string> }).options['variant'] : 'compass_minimal_black';
  return (
    <div data-testid="spec-chrome-north-arrow" className={`map-chrome absolute z-30 flex h-control-lg w-control-lg flex-col items-center justify-center gap-px rounded-chrome ${positionClass(component)}`} style={{ transform: `rotate(${-ctx.bearing}deg)` }} aria-label={`指北针（${variant}），当前方位角 ${Math.round(ctx.bearing)}°`}>
      <Glyph variant={variant} />
      <span aria-hidden className="text-micro font-semibold leading-none text-map-chrome-ink-muted">N</span>
    </div>
  );
}

registerComponentRenderer('north_arrow', NorthArrowRenderer);
