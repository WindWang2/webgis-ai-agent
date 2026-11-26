'use client';

import { useEffect, useRef } from 'react';
import { TILE_PROVIDERS } from '@/lib/providers';
import { useHudStore } from '@/lib/store/useHudStore';
import { useT } from '@/lib/i18n/useT';

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
  const t = useT();
  const readoutRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const readout = readoutRef.current;
    const shell = readout?.closest<HTMLElement>('.workspace-shell');
    if (!readout || !shell || typeof ResizeObserver === 'undefined') return;
    // Long provider credits can wrap. Keep the scale and legend above the actual box.
    const measure = () => shell.style.setProperty('--map-readout-height', readout.offsetHeight + 'px');
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(readout);
    return () => {
      observer.disconnect();
      shell.style.removeProperty('--map-readout-height');
    };
  }, []);

  const baseLayer = useHudStore((s) => s.baseLayer);
  const attribution = TILE_PROVIDERS.find((provider) => provider.name === baseLayer || provider.id === baseLayer)?.attribution?.replace(/<[^>]*>/g, '').replace(/&copy;/g, '©').replace(/&amp;/g, '&') ?? '';
  const viewport = useHudStore((s) => s.viewport);

  const [lng, lat] = viewport?.center ?? [0, 0];
  const zoom = viewport?.zoom ?? 0;

  return (
    <div
      ref={readoutRef}
      className='map-chrome flex flex-wrap items-center justify-end gap-x-2 gap-y-0.5 px-2 py-1 font-mono text-micro tabular-nums'
      // Not a live region: the values change on every pan frame and would flood
      // a screen reader. The static label carries the meaning instead.
      aria-label={t('map.readout.aria')}
    >
      <span className='inline-flex items-center gap-1.5 whitespace-nowrap text-map-chrome-ink'>
        <span>{Number.isFinite(lat) ? lat.toFixed(4) : '—'}, {Number.isFinite(lng) ? lng.toFixed(4) : '—'}</span>
        <span aria-hidden className='text-map-chrome-border'>│</span>
        <span>Z{Number.isFinite(zoom) ? zoom.toFixed(1) : '—'}</span>
        <span aria-hidden className='text-map-chrome-border'>│</span>
        <span className='text-map-chrome-ink-muted'>EPSG:4326</span>
      </span>
      {attribution && <span className='min-w-0 text-right text-map-chrome-ink-muted'>{attribution}</span>}
    </div>
  );
}

export default MapStatusReadout;
