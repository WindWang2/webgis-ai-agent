import type { GeoJSONSource, Map } from 'maplibre-gl';
import { useHudStore } from '@/lib/store/useHudStore';

// R8 annotation source: 单一 FeatureCollection 收纳 add_marker / draw_measurement 输出。
// Zustand 状态迁移：将原本模块级的 mutable array 迁移至 Zustand，支持多组件共享、响应式更新和一致的生命周期管理。
export const ANNOTATION_SOURCE_ID = 'claude-annotations';

/**
 * Reads the annotation FeatureCollection from the shared Zustand store.
 *
 * Extracted verbatim from map-action-handler.tsx. Shared between the annotation
 * slice (annotationCommands.ts) and the component's annotation-refresh useEffect.
 */
export function annotationFC() {
  const annotations = useHudStore.getState().annotations;
  return { type: 'FeatureCollection' as const, features: annotations.slice() };
}

/**
 * Idempotently mounts the annotation source + layers on the map.
 *
 * Extracted verbatim from map-action-handler.tsx. Shared between the annotation
 * slice and the component's annotation-refresh useEffect.
 */
export function ensureAnnotationLayers(map: Map) {
  if (!map.getSource(ANNOTATION_SOURCE_ID)) {
    map.addSource(ANNOTATION_SOURCE_ID, { type: 'geojson', data: annotationFC() as any });
  }
  if (!map.getLayer(`${ANNOTATION_SOURCE_ID}-fill`)) {
    map.addLayer({
      id: `${ANNOTATION_SOURCE_ID}-fill`,
      source: ANNOTATION_SOURCE_ID,
      type: 'fill',
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': '#3b82f6', 'fill-opacity': 0.25 },
    });
  }
  if (!map.getLayer(`${ANNOTATION_SOURCE_ID}-line`)) {
    map.addLayer({
      id: `${ANNOTATION_SOURCE_ID}-line`,
      source: ANNOTATION_SOURCE_ID,
      type: 'line',
      filter: ['any', ['==', ['geometry-type'], 'LineString'], ['==', ['geometry-type'], 'Polygon']],
      paint: { 'line-color': '#2563eb', 'line-width': 2 },
    });
  }
  if (!map.getLayer(`${ANNOTATION_SOURCE_ID}-circle`)) {
    map.addLayer({
      id: `${ANNOTATION_SOURCE_ID}-circle`,
      source: ANNOTATION_SOURCE_ID,
      type: 'circle',
      filter: ['==', ['geometry-type'], 'Point'],
      paint: {
        'circle-radius': 7,
        'circle-color': ['coalesce', ['get', 'color'], '#ef4444'],
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2,
      },
    });
  }
  if (!map.getLayer(`${ANNOTATION_SOURCE_ID}-label`)) {
    map.addLayer({
      id: `${ANNOTATION_SOURCE_ID}-label`,
      source: ANNOTATION_SOURCE_ID,
      type: 'symbol',
      layout: {
        'text-field': ['get', 'label'],
        'text-size': 12,
        'text-anchor': 'top',
        'text-offset': [0, 0.8],
        'text-allow-overlap': false,
      },
      paint: {
        'text-color': '#0f172a',
        'text-halo-color': '#ffffff',
        'text-halo-width': 1.5,
      },
    });
  }
}

/**
 * Pushes the latest annotation FeatureCollection into the map source.
 *
 * Returns whether the annotation source existed. When the source is absent the
 * push silently no-ops — callers must NOT claim a confirmed mutation in that
 * case (ROUND-2: annotation commands fail `target_not_found` instead of
 * returning a `confirmed: true` the map never earned).
 *
 * Extracted verbatim from map-action-handler.tsx. Shared between the annotation
 * slice and the component's annotation-refresh useEffect.
 */
export function refreshAnnotations(map: Map): boolean {
  const src = map.getSource(ANNOTATION_SOURCE_ID) as GeoJSONSource | undefined;
  if (src && typeof (src as any).setData === 'function') {
    src.setData(annotationFC() as any);
    return true;
  }
  return false;
}

/**
 * Annotation stack layer ids, bottom-to-top (matches ensureAnnotationLayers
 * mount order: fill → line → circle → label). Moving each to the top in this
 * order ends with label on top.
 */
const ANNOTATION_LAYER_IDS = [
  `${ANNOTATION_SOURCE_ID}-fill`,
  `${ANNOTATION_SOURCE_ID}-line`,
  `${ANNOTATION_SOURCE_ID}-circle`,
  `${ANNOTATION_SOURCE_ID}-label`,
];

/**
 * FIX-3-9 (#401): re-raise the imperative annotation stack above the spec
 * layers after a reconcile.
 *
 * MapSpecRuntime.syncLayerZOrder moves every spec sublayer to the TOP of the
 * style after any patch with layer changes (visibility toggles, opacity edits,
 * layer adds, 3D switches), burying the annotations — they are added once
 * imperatively and nothing else ever lifts them. Markers/measurements end up
 * under 0.3-opacity data fills; labels become fully invisible.
 *
 * Guarded the same way as raiseSelectionHighlight (map-panel.tsx FIX-3-2):
 * no-op when the stack isn't mounted, and moveLayer is wrapped so a layer
 * that vanished mid-reconcile is skipped silently. Callers run it after a
 * reconcile settles, alongside the selection-highlight re-raise.
 */
export function raiseAnnotationLayers(map: Map): void {
  if (!ANNOTATION_LAYER_IDS.some((id) => map.getLayer(id))) return;
  for (const id of ANNOTATION_LAYER_IDS) {
    if (!map.getLayer(id)) continue;
    try {
      // moveLayer without beforeId → end of the layer order (top), i.e.
      // directly above every spec layer syncLayerZOrder just stacked.
      map.moveLayer(id);
    } catch {
      // Layer vanished mid-reconcile — nothing to re-raise.
    }
  }
}
