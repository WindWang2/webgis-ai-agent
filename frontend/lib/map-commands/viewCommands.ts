import type { CommandEntry } from './types';
import * as navigation from '@/lib/map-kit/navigation';
import { devOnly } from '@/lib/utils/logger';

/**
 * View commands: camera/navigation.
 *
 * Each `run` body is the verbatim extraction of the corresponding `case` from
 * map-action-handler.tsx, reading from `ctx` instead of the closed-over scope.
 * Validators mirror the old `REQUIRED_PARAMS` table in map-action-renderer.tsx
 * so the renderer gate accepts exactly the same actions as before.
 */
export const viewCommands: Record<string, CommandEntry> = {
  fly_to: {
    requiredParams: (p) => Array.isArray(p.center) && p.center.length === 2,
    run(ctx) {
      const { map, params } = ctx;
      if (params?.center) {
        navigation.flyTo(map, {
          center: params.center,
          zoom: params?.zoom || 12,
          bearing: params.bearing,
          pitch: params.pitch,
        });
      }
    },
  },

  zoom_to_bbox: {
    requiredParams: (p) => Array.isArray(p.bbox) && p.bbox.length === 4,
    run(ctx) {
      const { map, params } = ctx;
      const bbox = params?.bbox as [number, number, number, number] | undefined;
      const padding = params?.padding ?? 50;
      if (!bbox || bbox.length < 4) return;
      try {
        navigation.fitBounds(map, bbox, padding);
      } catch (e) {
        devOnly.warn('[MapActionHandler] zoom_to_bbox failed:', e);
      }
    },
  },

  set_map_view: {
    requiredParams: (p) => Array.isArray(p.center) || typeof p.zoom === 'number',
    run(ctx) {
      const { map, params } = ctx;
      const { zoom, bearing, pitch } = params || {};
      if (zoom === undefined && bearing === undefined && pitch === undefined) return;
      const center = map.getCenter();
      navigation.flyTo(map, {
        center: [center.lng, center.lat],
        zoom: zoom !== undefined ? zoom : map.getZoom(),
        bearing: bearing !== undefined ? bearing : map.getBearing(),
        pitch: pitch !== undefined ? pitch : map.getPitch(),
      });
    },
  },
};
