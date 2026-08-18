import type { Map } from 'maplibre-gl';

/**
 * Runs `onReady` once the map's base style can accept mutations.
 *
 * react-map-gl yields the MapLibre instance at React-mount time, but the style
 * loads asynchronously afterwards; addSource/addLayer in that window throw
 * "Style is not done loading." (MapSpecRuntime.reconcile guards its own patch
 * path with an isStyleLoaded retry loop — this is the same contract for the
 * imperative command/annotation paths.)
 *
 * - `isStyleLoaded()` true (or absent, e.g. slim test doubles) → synchronous.
 * - Otherwise wait for `load` (initial style) or the first `styledata` that
 *   reports the style loaded (covers the post-setStyle reload window, where a
 *   second `load` is not guaranteed to fire).
 *
 * Returns a cancel function that detaches listeners and suppresses a pending
 * callback — React effects use it as cleanup so a remap/unmount cannot fire a
 * stale mount.
 */
export function whenStyleReady(map: Map, onReady: () => void): () => void {
  const styleLoaded = (): boolean => {
    const probe = map as unknown as { isStyleLoaded?: () => boolean };
    return typeof probe.isStyleLoaded === 'function' ? probe.isStyleLoaded() : true;
  };

  if (styleLoaded()) {
    onReady();
    return () => {};
  }

  let settled = false;
  // 'load' fires after the first visually complete render — the style is
  // usable then even if isStyleLoaded() lags a tick on the same frame.
  const fire = () => {
    if (settled) return;
    settled = true;
    map.off('load', fire);
    map.off('styledata', onStyleData);
    onReady();
  };
  const onStyleData = () => {
    if (styleLoaded()) fire();
  };
  map.once('load', fire);
  map.on('styledata', onStyleData);
  return () => {
    if (settled) return;
    settled = true;
    map.off('load', fire);
    map.off('styledata', onStyleData);
  };
}
