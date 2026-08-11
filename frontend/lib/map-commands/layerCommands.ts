import type { CommandEntry } from './types';
import { TILE_PROVIDERS } from '@/lib/providers';
import * as navigation from '@/lib/map-kit/navigation';
import * as renderer from '@/lib/map-kit/renderer';
import { devOnly } from '@/lib/utils/logger';
import { parseFilter } from './parseFilter';

/**
 * Layer commands: vector/raster add-remove, base layer switch, visibility/style
 * updates, reorder, and filter.
 *
 * Each `run` body is the verbatim extraction of the corresponding `case` from
 * map-action-handler.tsx, reading from `ctx` instead of the closed-over scope.
 * `useHudStore.getState()` becomes `ctx.getHudState()`. Validators mirror the
 * old `REQUIRED_PARAMS` table in map-action-renderer.tsx.
 *
 * Casing: the component lowercases `action.command` before the catalogue lookup,
 * and `dispatchAction` normalizes to lowercase at entry, so the catalogue only
 * registers lowercase keys (e.g. `base_layer_change`, not `BASE_LAYER_CHANGE`).
 */
export const layerCommands: Record<string, CommandEntry> = {
  add_layer: {
    // run body reads `layerId` (tests + AI emissions); `id` tolerated for legacy emissions
    requiredParams: (p) => typeof p.layerId === 'string' || typeof p.id === 'string',
    run(ctx) {
      const { map, params } = ctx;
      const { layerId, type, geojson, style, flyTo } = params;
      // V3: silent no-ops become explicit failed results (design §6) — missing
      // target → target_not_found, missing payload data → invalid_params.
      if (!layerId) return { status: 'failed', error: 'target_not_found' };
      if (!geojson) return { status: 'failed', error: 'invalid_params' };

      const id = `custom-${layerId}`;
      renderer.addGeoJsonSource(map, id, geojson);

      if (style && ((style as any).type === 'choropleth' || (style as any).type === 'lisa')) {
        renderer.addThematicLayer(map, id, geojson, style as any);
      } else {
        renderer.addVectorLayer(map, {
          id,
          type: (type || 'fill') as any,
          source: id,
          paint: style || {}
        });
      }

      if (flyTo) {
        const bbox = navigation.calculateBBox(geojson);
        if (bbox) {
          navigation.fitBounds(map, bbox, 50);
        }
      }
    },
  },

  add_raster_layer: {
    requiredParams: (p) => typeof p.url === 'string' || typeof p.image === 'string',
    run(ctx) {
      const { map, params } = ctx;
      const { id, url, image, bbox, opacity = 1.0 } = params;
      const imageUrl = image || url;
      // V3: silent no-ops become explicit failed results (design §6).
      if (!id) return { status: 'failed', error: 'target_not_found' };
      if (!imageUrl || !bbox) return { status: 'failed', error: 'invalid_params' };

      const sourceId = `custom-${id}`;
      const layerId = `${sourceId}-layer`;

      // bbox should be [west, south, east, north]
      const coordinates: [[number, number], [number, number], [number, number], [number, number]] = [
        [bbox[0], bbox[3]], // top-left
        [bbox[2], bbox[3]], // top-right
        [bbox[2], bbox[1]], // bottom-right
        [bbox[0], bbox[1]]  // bottom-left
      ];

      renderer.addImageSource(map, sourceId, imageUrl, coordinates);
      renderer.addVectorLayer(map, {
        id: layerId,
        type: 'raster',
        source: sourceId,
        paint: {
          'raster-opacity': opacity,
          'raster-fade-duration': 500
        }
      });

      navigation.fitBounds(map, bbox, 80);
    },
  },

  remove_layer: {
    // run body reads `layer_id || layerId`; `id` tolerated for legacy emissions
    requiredParams: (p) => typeof p.layer_id === 'string' || typeof p.layerId === 'string' || typeof p.id === 'string',
    run(ctx) {
      const { map, params, getHudState } = ctx;
      const { layer_id, layerId } = params || {};
      const target = layer_id || layerId;
      // V3: missing target → explicit failed result (was a silent return).
      if (!target) return { status: 'failed', error: 'target_not_found' };
      renderer.removeLayerStack(map, `custom-${target}`, true);
      // Sync removal to store so LayersTab stays in sync
      getHudState().removeLayer(target);
    },
  },

  base_layer_change: {
    requiredParams: (p) => typeof p.name === 'string' || typeof p.id === 'string',
    run(ctx) {
      const { params, setSelectedBaseLayer, getHudState } = ctx;
      const name = params?.name as string | undefined;
      // V3: a missing name is a param failure, not a target miss.
      if (!name) return { status: 'failed', error: 'invalid_params' };
      const search = name.toLowerCase();

      // 1. Exact name match (case-insensitive)
      let idx = TILE_PROVIDERS.findIndex(p => p.name.toLowerCase() === search);

      // 2. Bidirectional substring match
      if (idx === -1) {
        idx = TILE_PROVIDERS.findIndex(p => {
          const n = p.name.toLowerCase();
          return n.includes(search) || search.includes(n);
        });
      }

      // 3. Keyword index — ai команды like "卫星"/"dark"/"osm"命中对应条目
      if (idx === -1) {
        idx = TILE_PROVIDERS.findIndex(p =>
          p.keywords.some(k => search.includes(k.toLowerCase())),
        );
      }

      if (idx !== -1) {
        setSelectedBaseLayer(idx);
        // QA-2026-05-20 ISSUE-002 fix: keep useHudStore.baseLayer in sync so
        // the dropdown button label, HUD panel, and status bar all show the
        // canonical name after an AI-driven switch_base_layer call.
        getHudState().setBaseLayer(TILE_PROVIDERS[idx].name);
      } else {
        devOnly.warn('[MapActionHandler] Could not match base layer name:', name);
        // V3: no provider matched → explicit failed result (was a silent no-op
        // with only a dev warning).
        return { status: 'failed', error: 'target_not_found' };
      }
    },
  },

  layer_visibility_update: {
    requiredParams: (p) => typeof p.layer_id === 'string' || typeof p.id === 'string',
    run(ctx) {
      const { map, params, getHudState } = ctx;
      const { layer_id, visible, opacity, name, color } = params || {};
      // V3: missing target → explicit failed result (was a silent return).
      if (!layer_id) return { status: 'failed', error: 'target_not_found' };

      const style = map.getStyle();
      style.layers?.forEach((l: any) => {
        if (l.id.startsWith(`custom-${layer_id}-`)) {
          renderer.updateLayerStyle(map, l.id, {
            visibility: visible !== undefined ? (visible ? 'visible' : 'none') : undefined,
            opacity,
            color: color as string | undefined,
          });
        }
      });
      // Sync visibility/opacity/name/color back to store so LayersTab stays in sync
      const storeUpdates: Record<string, unknown> = {};
      if (visible !== undefined) storeUpdates.visible = visible;
      if (opacity !== undefined) storeUpdates.opacity = opacity;
      if (name !== undefined) storeUpdates.name = name;
      if (color !== undefined) storeUpdates.style = { ...(getHudState().layers.find((l: any) => l.id === layer_id)?.style ?? {}), color };
      if (Object.keys(storeUpdates).length > 0) {
        getHudState().updateLayer(layer_id, storeUpdates);
      }
    },
  },

  layer_style_update: {
    requiredParams: (p) => typeof p.layer_id === 'string' || typeof p.id === 'string',
    run(ctx) {
      const { map, params, getHudState } = ctx;
      const { layer_id, style } = params || {};
      // V3: silent no-ops become explicit failed results (design §6).
      if (!layer_id) return { status: 'failed', error: 'target_not_found' };
      if (!style) return { status: 'failed', error: 'invalid_params' };
      const mapStyle = map.getStyle();
      const s = style as any;
      mapStyle.layers?.forEach((l: any) => {
        if (l.id.startsWith(`custom-${layer_id}-`)) {
          renderer.updateLayerStyle(map, l.id, {
            color: s.color,
            strokeColor: s.strokeColor,
            strokeWidth: s.strokeWidth,
            pointSize: s.pointSize,
            dashArray: s.dashArray,
            fill: s.fill,
          });
        }
      });
      // Sync style changes back to store so LayersTab swatch stays in sync
      const styleUpdates: Record<string, any> = {};
      for (const key of ['color', 'strokeColor', 'strokeWidth', 'pointSize', 'dashArray', 'fill']) {
        if (s[key] !== undefined && s[key] !== null) styleUpdates[key] = s[key];
      }
      if (Object.keys(styleUpdates).length > 0) {
        const existing = getHudState().layers.find((l: any) => l.id === layer_id);
        getHudState().updateLayer(layer_id, {
          style: { ...(existing?.style ?? {}), ...styleUpdates },
        });
      }
    },
  },

  reorder_layer: {
    // run body reads `layer_id` + `position` (backend REORDER_LAYER emission);
    // the old validator (layers/order arrays) matched no actual run contract
    requiredParams: (p) => typeof p.layer_id === 'string' && typeof p.position === 'string',
    run(ctx) {
      const { map, params } = ctx;
      const { layer_id, position, before_id } = params || {};
      // V3: silent no-ops become explicit failed results (design §6).
      if (!layer_id || typeof layer_id !== 'string' || !layer_id.trim() || layer_id === 'ref:' || layer_id === 'custom-') {
        return { status: 'failed', error: 'target_not_found' };
      }
      if (!position) return { status: 'failed', error: 'invalid_params' };
      const style = map.getStyle();
      const allLayers = style.layers || [];
      const subIds = allLayers
        .map((l: any) => l.id as string)
        .filter((id: string) => id === `custom-${layer_id}` || id.startsWith(`custom-${layer_id}-`));
      if (subIds.length === 0) return { status: 'failed', error: 'target_not_found' };

      // Snapshot custom layer IDs only (we ignore base style layers)
      const customIds = allLayers
        .map((l: any) => l.id as string)
        .filter((id: string) => id.startsWith('custom-'));

      const firstSubIdx = customIds.indexOf(subIds[0]);
      let beforeAnchor: string | undefined;

      if (position === 'top') {
        beforeAnchor = undefined; // moveLayer with no anchor -> top
      } else if (position === 'bottom') {
        const bottomCandidate = customIds.find((id: string) => !subIds.includes(id));
        beforeAnchor = bottomCandidate;
      } else if (position === 'up') {
        // Find next custom group above
        for (let i = firstSubIdx - 1; i >= 0; i--) {
          if (!subIds.includes(customIds[i])) {
            // Place subIds before the layer that sits above customIds[i]
            beforeAnchor = customIds[i];
            break;
          }
        }
      } else if (position === 'down') {
        for (let i = firstSubIdx + subIds.length; i < customIds.length; i++) {
          if (!subIds.includes(customIds[i])) {
            beforeAnchor = customIds[i + 1];
            break;
          }
        }
      } else if (position === 'before' && before_id) {
        const targetGroup = customIds.find((id: string) => id === `custom-${before_id}` || id.startsWith(`custom-${before_id}-`));
        beforeAnchor = targetGroup;
      }

      try {
        for (const id of subIds) {
          map.moveLayer(id, beforeAnchor);
        }
      } catch (e) {
        devOnly.warn('[MapActionHandler] REORDER_LAYER failed:', e);
      }
    },
  },

  apply_layer_filter: {
    requiredParams: (p) => typeof p.layer_id === 'string' || typeof p.id === 'string',
    run(ctx) {
      const { map, params } = ctx;
      const { layer_id, filter } = params || {};
      // V3: missing target → explicit failed result (was a silent return).
      if (!layer_id) return { status: 'failed', error: 'target_not_found' };
      // Apply MapLibre filter with fallback parser for simple string filters
      map.setFilter(layer_id, parseFilter(filter) as any);
    },
  },
};
