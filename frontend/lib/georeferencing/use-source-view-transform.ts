import { useCallback, useState, type SetStateAction } from 'react';
import { computeFitTransform, type ImageDimensions, type ViewportSize, type ViewTransform } from './coords';

type Source = ImageDimensions & { source_id: string };

/** Fit and interactions belong to a particular source/session, not a passive effect. */
export function useSourceViewTransform(source: Source | null, scope: string | null, viewport: ViewportSize) {
  const key = JSON.stringify([scope, source?.source_id, source?.width, source?.height, viewport.width, viewport.height]);
  const fit = computeFitTransform(source ?? { width: 0, height: 0 }, viewport);
  const [owned, setOwned] = useState(() => ({ key, transform: fit }));
  // Adjust this component's own state during render so React never commits a
  // new source with the previous source's (or identity) pointer transform.
  // Same-key rerenders retain user pan/zoom; returning to a source refits it.
  if (owned.key !== key) setOwned({ key, transform: fit });
  const transform = owned.key === key ? owned.transform : fit;
  const setTransform = useCallback((update: SetStateAction<ViewTransform>) => {
    setOwned((previous) => {
      if (previous.key !== key) return previous; // late event from the old source
      return { key, transform: typeof update === 'function' ? update(previous.transform) : update };
    });
  }, [key]);
  return [transform, setTransform] as const;
}
