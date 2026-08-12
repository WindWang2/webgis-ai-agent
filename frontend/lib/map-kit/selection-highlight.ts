import type { GeoJSONSource, Map } from 'maplibre-gl';

/**
 * Selection highlight overlay (Harness–Map Interaction V3, FE-3).
 *
 * Follows the annotationHelpers.ts pattern: an imperative GeoJSON source +
 * paint layers mounted directly on the MapLibre instance, OUTSIDE the
 * MapSpecRuntime. The highlight is ephemeral interaction feedback — it must
 * never enter the declarative MapSpec (ADR-0036: the spec is derived from HUD
 * state; a click selection is not).
 *
 * Mounts its own copy of the selected feature's geometry, so it is immune to
 * the viewport re-filtering / source updates of the underlying data layer.
 */
export const SELECTION_HIGHLIGHT_SOURCE_ID = 'claude-selection-highlight';

const EMPTY_FC = { type: 'FeatureCollection' as const, features: [] };

/** Idempotently mount the selection highlight source + layers. */
export function ensureSelectionHighlightLayers(map: Map): void {
  if (!map.getSource(SELECTION_HIGHLIGHT_SOURCE_ID)) {
    map.addSource(SELECTION_HIGHLIGHT_SOURCE_ID, { type: 'geojson', data: EMPTY_FC as any });
  }
  if (!map.getLayer(`${SELECTION_HIGHLIGHT_SOURCE_ID}-fill`)) {
    map.addLayer({
      id: `${SELECTION_HIGHLIGHT_SOURCE_ID}-fill`,
      source: SELECTION_HIGHLIGHT_SOURCE_ID,
      type: 'fill',
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': '#facc15', 'fill-opacity': 0.25 },
    });
  }
  if (!map.getLayer(`${SELECTION_HIGHLIGHT_SOURCE_ID}-line`)) {
    map.addLayer({
      id: `${SELECTION_HIGHLIGHT_SOURCE_ID}-line`,
      source: SELECTION_HIGHLIGHT_SOURCE_ID,
      type: 'line',
      filter: ['any', ['==', ['geometry-type'], 'LineString'], ['==', ['geometry-type'], 'Polygon']],
      paint: { 'line-color': '#eab308', 'line-width': 3 },
    });
  }
  if (!map.getLayer(`${SELECTION_HIGHLIGHT_SOURCE_ID}-circle`)) {
    map.addLayer({
      id: `${SELECTION_HIGHLIGHT_SOURCE_ID}-circle`,
      source: SELECTION_HIGHLIGHT_SOURCE_ID,
      type: 'circle',
      filter: ['==', ['geometry-type'], 'Point'],
      paint: {
        'circle-radius': 10,
        'circle-color': 'rgba(250, 204, 21, 0.35)',
        'circle-stroke-color': '#eab308',
        'circle-stroke-width': 2,
      },
    });
  }
}

/**
 * Highlight a feature (a GeoJSON Feature-shaped object, e.g. the result of
 * queryRenderedFeatures). Pass null to clear. Only the geometry + properties
 * are copied into the overlay source.
 */
export function setSelectionHighlight(map: Map, feature: { geometry: unknown; properties?: unknown } | null): void {
  ensureSelectionHighlightLayers(map);
  const src = map.getSource(SELECTION_HIGHLIGHT_SOURCE_ID) as GeoJSONSource | undefined;
  if (!src || typeof (src as any).setData !== 'function') return;
  const fc = feature
    ? {
        type: 'FeatureCollection' as const,
        features: [
          {
            type: 'Feature' as const,
            geometry: feature.geometry,
            properties: (feature.properties ?? {}) as Record<string, unknown>,
          },
        ],
      }
    : EMPTY_FC;
  src.setData(fc as any);
}

/** Clear the current selection highlight (no-op when nothing is mounted). */
export function clearSelectionHighlight(map: Map): void {
  const src = map.getSource(SELECTION_HIGHLIGHT_SOURCE_ID) as GeoJSONSource | undefined;
  if (src && typeof (src as any).setData === 'function') {
    src.setData(EMPTY_FC as any);
  }
}
