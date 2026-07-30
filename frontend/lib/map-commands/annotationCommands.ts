import type { CommandEntry } from './types';
import { ensureAnnotationLayers, refreshAnnotations } from './annotationHelpers';

/**
 * Annotation commands (R8 single FeatureCollection source).
 *
 * Each `run` body is the verbatim extraction of the corresponding `case` from
 * map-action-handler.tsx, reading from `ctx` instead of the closed-over scope.
 * `useHudStore.getState()` becomes `ctx.getHudState()`; the annotation helpers
 * come from the shared annotationHelpers.ts module. Validators mirror the old
 * `REQUIRED_PARAMS` table in map-action-renderer.tsx.
 */
export const annotationCommands: Record<string, CommandEntry> = {
  add_marker: {
    requiredParams: (p) => Array.isArray(p.center) || Array.isArray(p.coordinate),
    run(ctx) {
      const { map, params, getHudState } = ctx;
      const { longitude, latitude, label, color } = params || {};
      if (typeof longitude !== 'number' || typeof latitude !== 'number') return;
      ensureAnnotationLayers(map);
      getHudState().addAnnotation({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [longitude, latitude] },
        properties: { label: label || null, color: color || '#ef4444', kind: 'marker' },
      });
      refreshAnnotations(map);
    },
  },

  draw_measurement: {
    requiredParams: () => true,
    run(ctx) {
      const { map, params, getHudState } = ctx;
      const { shape, coordinates, label } = params || {};
      if (!Array.isArray(coordinates) || coordinates.length < 2) return;
      ensureAnnotationLayers(map);
      const store = getHudState();
      if (shape === 'polygon') {
        const ring = coordinates.slice();
        // 闭合环
        if (ring.length > 0) {
          const first = ring[0];
          const last = ring[ring.length - 1];
          if (first[0] !== last[0] || first[1] !== last[1]) ring.push([first[0], first[1]]);
        }
        store.addAnnotation({
          type: 'Feature',
          geometry: { type: 'Polygon', coordinates: [ring] },
          properties: { label: label || null, kind: 'measure_polygon' },
        });
        // 也撒一个 label 点在质心，便于地图上看到数值
        const cx = ring.reduce((s, p) => s + p[0], 0) / ring.length;
        const cy = ring.reduce((s, p) => s + p[1], 0) / ring.length;
        if (label) {
          store.addAnnotation({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [cx, cy] },
            properties: { label, color: 'transparent', kind: 'measure_label' },
          });
        }
      } else {
        // 默认 polyline
        store.addAnnotation({
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: coordinates.slice() },
          properties: { label: label || null, kind: 'measure_line' },
        });
        if (label) {
          const end = coordinates[coordinates.length - 1];
          store.addAnnotation({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: end.slice() },
            properties: { label, color: 'transparent', kind: 'measure_label' },
          });
        }
      }
      refreshAnnotations(map);
    },
  },

  clear_annotations: {
    requiredParams: () => true,
    run(ctx) {
      const { map, getHudState } = ctx;
      getHudState().clearAnnotations();
      refreshAnnotations(map);
    },
  },
};
