'use client';

import { useHudStore } from '@/lib/store/useHudStore';

/**
 * Bottom-right map readout: cursor-independent centre coordinate, zoom level,
 * CRS and attribution.
 *
 * A desktop GIS always tells you *where* and *how far in* you are. Before V4 the
 * app showed only "© OpenStreetMap contributors" in a hardcoded light pill, so a
 * user had no zoom or CRS reference at all and the pill turned into a bright
 * block on the dark map.
 *
 * It subscribes to `viewport` itself rather than taking it as a prop, which
 * keeps the map-pan render path out of the page component (`app/page.tsx`
 * re-renders on every streamed token batch, and its siblings are memoised to
 * stay off that path).
 */
export function MapStatusReadout() {
  const viewport = useHudStore((s) => s.viewport);

  const [lng, lat] = viewport?.center ?? [0, 0];
  const zoom = viewport?.zoom ?? 0;

  return (
    <div
      className='map-chrome flex items-center gap-2 px-2 py-1 font-mono text-micro tabular-nums'
      // Not a live region: the values change on every pan frame and would flood
      // a screen reader. The static label carries the meaning instead.
      aria-label='地图视图状态'
    >
      <span className='text-map-chrome-ink'>
        {Number.isFinite(lat) ? lat.toFixed(4) : '—'}, {Number.isFinite(lng) ? lng.toFixed(4) : '—'}
      </span>
      <span aria-hidden className='text-map-chrome-border'>│</span>
      <span className='text-map-chrome-ink'>Z{Number.isFinite(zoom) ? zoom.toFixed(1) : '—'}</span>
      <span aria-hidden className='text-map-chrome-border'>│</span>
      <span className='text-map-chrome-ink-muted'>EPSG:4326</span>
      <span aria-hidden className='text-map-chrome-border'>│</span>
      <span className='text-map-chrome-ink-muted'>© OpenStreetMap</span>
    </div>
  );
}

export default MapStatusReadout;
