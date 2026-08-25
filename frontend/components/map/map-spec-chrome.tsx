'use client';

import React from 'react';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { renderComponent } from './map-components';
import { metersPerPixelAt } from '@/lib/map-kit/meters-per-pixel';

export function computeScale(zoom: number, lat: number): { meters: number; pixels: number } {
  const metersPerPixel = metersPerPixelAt(zoom, lat);
  const targetMeters = metersPerPixel * 100;
  const candidates = [50, 100, 200, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 50_000, 100_000];
  let best = candidates[0];
  for (const c of candidates) if (c <= targetMeters) best = c;
  return { meters: best, pixels: best / metersPerPixel };
}

interface ChromeProps {
  components: MapSpecComponent[];
  zoom: number;
  centerLat: number;
  bearing: number;
  spec: MapSpec | null;
}

export const MapSpecChrome = React.memo(function MapSpecChrome({ components, zoom, centerLat, bearing, spec }: ChromeProps) {
  const enabled = components.filter((c) => c.enabled !== false);
  if (!enabled.length) return null;

  const hasType = (t: string) => components.some((c) => c.type === t);
  const fallbackDecor: MapSpecComponent[] = [];
  if (!hasType('north_arrow')) {
    fallbackDecor.push({ id: '__fallback_north_arrow', type: 'north_arrow', enabled: true } as MapSpecComponent);
  }
  if (!hasType('scale_bar')) {
    fallbackDecor.push({ id: '__fallback_scale_bar', type: 'scale_bar', enabled: true } as MapSpecComponent);
  }
  const renderable = fallbackDecor.length ? [...enabled, ...fallbackDecor] : enabled;

  const ctx = { spec, zoom, centerLat, bearing };

  return (
    <>
      {renderable.map((c, i) => {
        const node = renderComponent(c, ctx);
        if (!node) return null;
        return <React.Fragment key={`${c.id}#${i}`}>{node}</React.Fragment>;
      })}
    </>
  );
});
