import type { CommandEntry } from './types';
import type { ThematicStyleDef } from '@/lib/map-kit/types';
import * as navigation from '@/lib/map-kit/navigation';
import * as renderer from '@/lib/map-kit/renderer';
import { rememberCustomOverlay } from './custom-overlay-registry';

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
      // #1078(G-1): setStyle 后重挂登记（与 add_raster_layer 同型）。
      rememberCustomOverlay(id, (m) => {
        renderer.addImageSource(m, id, image, coords);
        renderer.addVectorLayer(m, {
          id,
          type: 'raster',
          source: id,
          paint: { 'raster-opacity': opacity || 0.7 },
        });
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
      // #611: 后端模板（app/tools/templates.py apply_template heatmap variant）发射
      // 的 params 含 heatPalette/intensity/field（未列入 MapActionPayload 类型收窄）。
      const { heatPalette, intensity, field, radiusPx } = (params ?? {}) as Partial<{
        heatPalette: string[];
        intensity: number;
        field: string;
        radiusPx: number;
      }>;
      // heatmap_data 工具结果把 palette/radius_px/radius/intensity 嵌在 metadata
      // 里（useMapBridge 把除 command 外的整体作为 params 透传）——不读的话
      // agent 选的配色/半径会静默丢失。半径契约：优先显式 radius_px（像素）；
      // legacy radius 由 renderer 归一化消化（4-60 直通，超窗默认 30px），
      // 米值绝不再被当作像素消费（后端 heatmap_contract 前端镜像）。
      const meta = ((params ?? {}) as { metadata?: { palette?: string; radius?: number; radius_px?: number; intensity?: number } }).metadata ?? {};
      // V3: missing payload data → explicit failed result (was a silent return).
      if (!geojson) return { status: 'failed', error: 'invalid_params' };

      // heatPalette 颜色数组（新后端形态）与 palette 命名键（老调用方）都读，自定义
      // 配色不再静默丢失。layerId 缺失时用 field 派生稳定 id（Date.now() 会让同模板
      // 每次应用叠加一个匿名层且无法被 layer_ref/removeOrphanCustomLayers 命中）。
      const id = `custom-${layerId || 'native-heatmap-' + (field || 'default')}`;

      renderer.addGeoJsonSource(map, id, geojson);
      renderer.addNativeHeatmap(map, {
        id,
        source: id,
        palette: (heatPalette ?? palette ?? meta.palette) as any,
        radiusPx: radiusPx ?? meta.radius_px,
        radius: radius ?? meta.radius,
        intensity: intensity ?? meta.intensity,
        opacity: 0.8
      });
      // #1078(G-1): setStyle 后重挂登记。
      rememberCustomOverlay(id, (m) => {
        renderer.addGeoJsonSource(m, id, geojson);
        renderer.addNativeHeatmap(m, {
          id,
          source: id,
          palette: (heatPalette ?? palette ?? meta.palette) as any,
          radiusPx: radiusPx ?? meta.radius_px,
          radius: radius ?? meta.radius,
          intensity: intensity ?? meta.intensity,
          opacity: 0.8,
        });
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
      // #1078(G-1): setStyle 后重挂登记。
      rememberCustomOverlay(id, (m) => {
        renderer.addGeoJsonSource(m, id, geojson);
        renderer.addThematicLayer(m, id, geojson, style);
      });
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
