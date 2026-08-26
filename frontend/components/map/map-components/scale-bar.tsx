'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass, resolveVariant, stackedBottomStyle } from './helpers';
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

// D7：academic —— 黑白交替分段尺（经典制图比例尺），4 段等分。
function AcademicSegments({ pixels }: { pixels: number }) {
  const segments = 4;
  const segWidth = Math.max(2, Math.round(pixels / segments));
  return (
    <div aria-hidden className="flex border border-map-chrome-ink" style={{ height: '7px' }}>
      {Array.from({ length: segments }, (_, i) => (
        <div
          key={i}
          style={{ width: `${segWidth}px`, background: i % 2 === 0 ? 'var(--map-chrome-text)' : 'transparent' }}
        />
      ))}
    </div>
  );
}

function ScaleBarRenderer(component: MapSpecComponent, ctx: RendererContext) {
  const { meters, pixels } = computeScale(ctx.zoom, ctx.centerLat);
  // D7：minimal（缺省，现状）| boxed（卡片）| academic（黑白分段）
  const variant = resolveVariant(component, 'minimal');
  const width = Math.round(pixels);
  return (
    <div
      data-testid="spec-chrome-scale-bar"
      data-variant={variant}
      style={stackedBottomStyle(component, ctx.bottomSlotIndexes)}
      className={`map-chrome absolute z-30 flex items-center gap-2 text-caption font-medium tabular-nums ${positionClass(component)} ${
        variant === 'boxed' ? 'rounded-chrome px-2.5 py-1.5' : variant === 'academic' ? 'rounded-chrome px-2 py-1' : 'px-2 py-1'
      }`}
      aria-label={`比例尺 ${formatMeters(meters)}`}
    >
      {variant === 'academic' ? (
        <>
          <span className="text-micro tabular-nums text-map-chrome-ink-muted">0</span>
          <AcademicSegments pixels={pixels} />
          <span className="text-map-chrome-ink">{formatMeters(meters)}</span>
        </>
      ) : (
        <>
          <div aria-hidden className="border-b-2 border-l-2 border-r-2 border-map-chrome-ink" style={{ width: `${width}px`, height: '5px' }} />
          <span className="text-map-chrome-ink">{formatMeters(meters)}</span>
        </>
      )}
    </div>
  );
}

registerComponentRenderer('scale_bar', ScaleBarRenderer);
