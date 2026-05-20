'use client';

import React from 'react';
import { Compass } from 'lucide-react';

interface Props {
  show: boolean;
  title: string | null;
  zoom: number;
  centerLat: number;
  bearing: number;
}

// Convert zoom + latitude to meters/pixel, then snap to a human-friendly scale
function computeScale(zoom: number, lat: number): { meters: number; pixels: number } {
  const EARTH_CIRCUMFERENCE = 40_075_016.686;
  const metersPerPixel = (EARTH_CIRCUMFERENCE * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom + 8);
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

export function MapDecorations({ show, title, zoom, centerLat, bearing }: Props) {
  if (!show) return null;
  const { meters, pixels } = computeScale(zoom, centerLat);

  return (
    <>
      {title && (
        <div
          data-testid="map-title"
          className="absolute top-3 left-1/2 -translate-x-1/2 z-30 px-4 py-1.5 rounded-full bg-card/90 backdrop-blur-md border border-border shadow-lg text-sm font-semibold text-foreground"
        >
          {title}
        </div>
      )}
      <div
        data-testid="north-arrow"
        className="absolute top-3 right-3 z-30 p-2 rounded-full bg-card/90 backdrop-blur-md border border-border shadow-lg"
        style={{ transform: `rotate(${-bearing}deg)` }}
        aria-label="指北针"
      >
        <Compass className="h-4 w-4 text-foreground" />
      </div>
      <div
        data-testid="scale-bar"
        className="absolute bottom-10 right-3 z-30 px-2 py-1 rounded-md bg-card/90 backdrop-blur-md border border-border shadow-lg text-[11px] font-medium text-foreground flex items-center gap-2"
      >
        <div
          className="border-b-2 border-l-2 border-r-2 border-foreground"
          style={{ width: `${Math.round(pixels)}px`, height: '6px' }}
        />
        <span>{formatMeters(meters)}</span>
      </div>
    </>
  );
}
