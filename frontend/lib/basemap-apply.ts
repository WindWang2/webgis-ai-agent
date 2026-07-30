import { ProviderId, TILE_PROVIDERS } from "./providers";

export interface RasterFilters {
  brightness?: number;
  contrast?: number;
  saturation?: number;
  hueRotate?: number;
  opacity?: number;
}

export interface OverlayLayer {
  providerId: string;
  vectorStyleUrl?: string;
  opacity?: number;
}

export interface BasemapPayload {
  providerId: string;
  rasterFilters?: RasterFilters;
  vectorStyleUrl?: string;
  overlays?: OverlayLayer[];
}

export interface MapLibreStyleSpec {
  version: 8;
  name?: string;
  vectorStyleUrl?: string;
  sources: Record<string, any>;
  layers: any[];
}

/**
 * applyBaseline - Turns a BasemapPayload into a MapLibre style specification.
 *
 * Single branch point: presence of vectorStyleUrl (vector) vs raster tile.
 */
export function applyBaseline(payload: BasemapPayload): MapLibreStyleSpec {
  // 1. Vector branch: vectorStyleUrl presence
  if (payload.vectorStyleUrl) {
    const style: MapLibreStyleSpec = {
      version: 8,
      vectorStyleUrl: payload.vectorStyleUrl,
      sources: {},
      layers: [],
    };
    applyOverlays(style, payload.overlays);
    return style;
  }

  // 2. Raster branch: lookup provider & build style with paint properties for rasterFilters
  const provider = TILE_PROVIDERS.find((p) => p.id === payload.providerId);
  const tileUrl = provider ? provider.url : `https://tile.openstreetmap.org/{z}/{x}/{y}.png`;
  const sourceId = `raster-${payload.providerId}`;
  const layerId = `raster-layer-${payload.providerId}`;

  const filters = payload.rasterFilters || {};
  const paint: Record<string, any> = {};

  if (filters.opacity !== undefined) {
    paint["raster-opacity"] = filters.opacity;
  }
  if (filters.contrast !== undefined) {
    paint["raster-contrast"] = filters.contrast;
  }
  if (filters.saturation !== undefined) {
    paint["raster-saturation"] = filters.saturation;
  }
  if (filters.hueRotate !== undefined) {
    paint["raster-hue-rotate"] = filters.hueRotate;
  }
  if (filters.brightness !== undefined) {
    paint["raster-brightness-max"] = filters.brightness;
  }

  const style: MapLibreStyleSpec = {
    version: 8,
    name: provider ? provider.name : payload.providerId,
    sources: {
      [sourceId]: {
        type: "raster",
        tiles: [tileUrl],
        tileSize: 256,
      },
    },
    layers: [
      {
        id: layerId,
        type: "raster",
        source: sourceId,
        minzoom: 0,
        maxzoom: 19,
        paint,
      },
    ],
  };

  applyOverlays(style, payload.overlays);
  return style;
}

function applyOverlays(style: MapLibreStyleSpec, overlays?: OverlayLayer[]) {
  if (!overlays || overlays.length === 0) return;

  overlays.forEach((overlay, idx) => {
    const overlaySourceId = `overlay-source-${idx}-${overlay.providerId}`;
    const overlayLayerId = `overlay-layer-${idx}-${overlay.providerId}`;

    if (overlay.vectorStyleUrl) {
      style.layers.push({
        id: overlayLayerId,
        type: "vector-overlay",
        vectorStyleUrl: overlay.vectorStyleUrl,
        paint: {
          "raster-opacity": overlay.opacity ?? 1.0,
        },
      });
    } else {
      const provider = TILE_PROVIDERS.find((p) => p.id === overlay.providerId);
      const tileUrl = provider ? provider.url : overlay.providerId;

      style.sources[overlaySourceId] = {
        type: "raster",
        tiles: [tileUrl],
        tileSize: 256,
      };

      style.layers.push({
        id: overlayLayerId,
        type: "raster",
        source: overlaySourceId,
        paint: {
          "raster-opacity": overlay.opacity ?? 1.0,
        },
      });
    }
  });
}
