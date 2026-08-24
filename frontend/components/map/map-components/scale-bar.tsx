'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass, positionStyle } from './helpers';
import type { RendererContext } from './types';
import { metersPerPixelAt } from '@/lib/map-kit/meters-per-pixel';

function computeScale(zoom: number, lat: number) {
  const mpp = metersPerPixelAt(zoom, lat);
  const target = mpp * 100;
  const candidates = [50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000];
  let best = candidates[0];
  for (const c of candidates) if (c <= target) best = c;
  return { meters: best, pixels: best / mpp };
}
function formatMeters(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(m % 1000 === 0 ? 0 : 1)} km` : `${m} m`;
}

function ScaleBarRenderer(component: MapSpecComponent, ctx: RendererContext) {
  const { meters, pixels } = computeScale(ctx.zoom, ctx.centerLat);
  return (
    <div data-testid="spec-chrome-scale-bar" style={positionStyle(component)} className={`map-chrome absolute z-30 flex items-center gap-2 px-2 py-1 text-caption font-medium tabular-nums ${positionClass(component)}`} aria-label={`比例尺 ${formatMeters(meters)}`}>
      <div aria-hidden className="border-b-2 border-l-2 border-r-2 border-map-chrome-ink" style={{ width: `${Math.round(pixels)}px`, height: '5px' }} />
      <span className="text-map-chrome-ink">{formatMeters(meters)}</span>
    </div>
  );
}

registerComponentRenderer('scale_bar', ScaleBarRenderer);
