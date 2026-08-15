import type { CommandEntry } from './types';
import type { ThematicStyleDef } from '@/lib/map-kit/types';
import * as navigation from '@/lib/map-kit/navigation';
import * as renderer from '@/lib/map-kit/renderer';

/**
 * Heatmap / thematic-map commands.
 *
 * Each `run` body is the verbatim extraction of the corresponding `case` from
 * map-action-handler.tsx, reading from `ctx` instead of the closed-over scope.
 * Validators mirror the old `REQUIRED_PARAMS` table in map-action-renderer.tsx.
 */
export const heatmapCommands: Record<string, CommandEntry> = {
  add_heatmap_raster: {
    requiredParams: (p) => typeof p.url === 'string' || typeof p.image === 'string',
    run(ctx) {
      const { map, params } = ctx;
      const { image, bbox, opacity, layerId } = params || {};
      // V3: missing payload data → explicit failed result (was a silent return).
      if (!image) return { status: 'failed', error: 'invalid_params' };
      if (!bbox) return { status: 'failed', error: 'invalid_params' };

      const id = `custom-${layerId || 'heatmap-' + Date.now()}`;

      // bbox is [west, south, east, north]
      // MapLibre image source expects: [top-left, top-right, bottom-right, bottom-left]
      const coords: [[number, number], [number, number], [number, number], [number, number]] = [
        [bbox[0], bbox[3]], // west, north
        [bbox[2], bbox[3]], // east, north
        [bbox[2], bbox[1]], // east, south
        [bbox[0], bbox[1]]  // west, south
      ];

      renderer.addImageSource(map, id, image, coords);
      renderer.addVectorLayer(map, {
        id,
        type: 'raster',
        source: id,
        paint: { 'raster-opacity': opacity || 0.7 }
      });

      navigation.fitBounds(map, bbox, 50);
      // V3 round-2 FIX-B (issue #393): post-mutation verification — the image
      // source and the raster layer must actually exist on the map before we
      // claim success. The old body returned void unconditionally, so the
      // dispatcher acked succeeded even when the mount silently failed.
      if (!map.getSource?.(id) || !map.getLayer?.(id)) {
        return { status: 'failed', error: 'mutation_failed' };
      }
      // V3: verifiable marker (heatmap raster add — harness convergence).
      return { status: 'succeeded', result: { confirmed: true } };
    },
  },

  add_native_heatmap: {
    requiredParams: (p) => !!p.geojson || typeof p.id === 'string',
    run(ctx) {
      const { map, params } = ctx;
      const { geojson, layerId, palette, radius } = params || {};
      // V3: missing payload data → explicit failed result (was a silent return).
      if (!geojson) return { status: 'failed', error: 'invalid_params' };

      const id = `custom-${layerId || 'native-heatmap-' + Date.now()}`;

      renderer.addGeoJsonSource(map, id, geojson);
      renderer.addNativeHeatmap(map, {
        id,
        source: id,
        palette: palette as any,
        radius,
        opacity: 0.8
      });
      // V3 round-2 FIX-B (issue #393): post-mutation verification — both the
      // source and the heatmap layer must exist before claiming success (was:
      // unconditional void → fake succeeded ack).
      if (!map.getSource?.(id) || !map.getLayer?.(id)) {
        return { status: 'failed', error: 'mutation_failed' };
      }
      // V3: verifiable marker (native heatmap add — harness convergence).
      return { status: 'succeeded', result: { confirmed: true } };
    },
  },

  create_thematic_map: {
    requiredParams: () => true,
    run(ctx) {
      const { map, params } = ctx;
      // Choropleth / LISA thematic map. Backend computes the breaks/colors (style_def)
      // and legend; the frontend just needs to mount a step/match-expression layer.
      // Note: legend_spec is carried by the result but no legend panel consumes it yet
      // (tracked separately); mount is the gating behavior for the apply path.
      const { geojson, layerId, style, field } = (params || {}) as {
        geojson?: any; layerId?: string; style?: ThematicStyleDef; field?: string;
      };
      // V3: missing payload data → explicit failed result (was a silent return).
      if (!geojson || !style) return { status: 'failed', error: 'invalid_params' };

      const id = `custom-${layerId || 'thematic-' + (field || Date.now())}`;
      renderer.addGeoJsonSource(map, id, geojson);
      renderer.addThematicLayer(map, id, geojson, style);
      // V3 round-2 FIX-B (issue #393): post-mutation verification — the source
      // and the thematic layer must exist before claiming success (was:
      // unconditional void → fake succeeded ack).
      if (!map.getSource?.(id) || !map.getLayer?.(id)) {
        return { status: 'failed', error: 'mutation_failed' };
      }
      // V3: verifiable marker (thematic map mount — harness convergence).
      return { status: 'succeeded', result: { confirmed: true } };
    },
  },
};
