'use client';

import React from 'react';
import { Compass } from 'lucide-react';
import { metersPerPixelAt } from '@/lib/map-kit/meters-per-pixel';

interface Props {
  show: boolean;
  title: string | null;
  zoom: number;
  centerLat: number;
  bearing: number;
}

// Convert zoom + latitude to meters/pixel, then snap to a human-friendly scale
export function computeScale(zoom: number, lat: number): { meters: number; pixels: number } {
  const metersPerPixel = metersPerPixelAt(zoom, lat);
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

export const MapDecorations = React.memo(function MapDecorations({ show, title, zoom, centerLat, bearing }: Props) {
  if (!show) return null;
  const { meters, pixels } = computeScale(zoom, centerLat);

  return (
    <>
      {title && (
        <div
          data-testid="map-title"
          className="map-chrome absolute top-3 left-1/2 -translate-x-1/2 z-30 max-w-[min(46ch,50%)] truncate px-3 py-1 text-title font-semibold"
        >
          {title}
        </div>
      )}
      {/* North arrow with an explicit N label — a bare compass glyph is not the
          GIS convention, and the rotation makes "which way is north" the whole
          point of the control. */}
      <div
        data-testid="north-arrow"
        className="map-chrome absolute top-3 right-3 z-30 flex h-control-lg w-control-lg flex-col items-center justify-center gap-px rounded-chrome"
        style={{ transform: `rotate(${-bearing}deg)` }}
        aria-label={`指北针，当前方位角 ${Math.round(bearing)}°`}
      >
        <Compass aria-hidden className="h-icon-md w-icon-md text-map-chrome-ink" />
        <span aria-hidden className="text-micro font-semibold leading-none text-map-chrome-ink-muted">N</span>
      </div>
      {/* Scale bar sits in the bottom-right chrome column, one step above the
          status readout. Before V4 it was a fixed `bottom-14` that left a 6px
          gap to the attribution pill and collided as soon as either grew. */}
      <div
        data-testid="scale-bar"
        className="map-chrome absolute right-3 z-30 flex items-center gap-2 px-2 py-1 text-caption font-medium tabular-nums transition-[bottom] duration-300"
        style={{ bottom: 'calc(var(--map-chrome-bottom, 10px) + 30px)' }}
        aria-label={`比例尺 ${formatMeters(meters)}`}
      >
        <div
          aria-hidden
          className="border-b-2 border-l-2 border-r-2 border-map-chrome-ink"
          style={{ width: `${Math.round(pixels)}px`, height: '5px' }}
        />
        <span className="text-map-chrome-ink">{formatMeters(meters)}</span>
      </div>
    </>
  );
});
