'use client';
import React from 'react';
import { Compass, Navigation2, Rose } from 'lucide-react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass } from './helpers';
import type { RendererContext } from './types';

function Glyph({ variant }: { variant: string }) {
  if (variant === 'compass_needle') return <Navigation2 aria-hidden className="h-icon-md w-icon-md text-map-chrome-ink" />;
  if (variant === 'compass_rose') return <Rose aria-hidden className="h-icon-md w-icon-md text-map-chrome-ink" />;
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
