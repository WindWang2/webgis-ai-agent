import type { Layer } from "@/lib/types/layer";
import type { GeoJSONFeatureCollection, HeatmapRasterSource } from "@/lib/types";
import type { MapSpec, MapSpecSource, MapSpecLayer, MapSpecLayerPaint } from "@/lib/mapspec-compiler/types";
import { legendSpecToColorExpression, thematicField } from "@/lib/mapspec-runtime/thematic-paint";

/**
 * hudStateToMapSpec — pure adapter (ADR-0036, Q2 = "derived MapSpec").
 *
 * Projects the HUD store's Layer[] (+ processLayers + activeFilters + is3D)
 * into a flat MapSpec. This is the load-bearing piece of the MapSpecRuntime:
 * it must produce byte-identical MapLibre paint/filter/expressions to the
 * inline render loop it replaces in map-panel.tsx (lines 159-383 of the
 * pre-refactor file). adapter.test.ts pins that contract.
 *
 * Flattening rules (mirror map-panel.tsx exactly):
 *  - One HUD Layer fans out into 0..N MapSpecLayers depending on its source's
 *    geometry mix and rendering mode.
 *  - Sublayers are keyed `${layer.id}__${sub}` so diffSpecs addresses them
 *    stably across reconciles (the runtime owns the MapLibre id scheme).
 *  - Visibility is encoded as layout.visibility rather than add/omit, so a
 *    visibility toggle is a cheap recompile rather than add+remove churn.
 */

export const SUBLAYER_SEP = "__";

/** Data Plane: 超过该要素数的 ref 图层改用 MVT 矢量瓦片显示。 */
export const VECTOR_TILE_THRESHOLD = 5000;

export interface HudToSpecInput {
  layers: Layer[];
  processLayers: Record<string, GeoJSONFeatureCollection>;
  activeFilters: Record<string, number[][]>;
  is3D: boolean;
}

// ---- source-shape discriminators (mirror map-panel.tsx:60-66) ----

function isHeatmapRasterSource(source: Layer["source"]): source is HeatmapRasterSource {
  return typeof source === "object" && source !== null && "image" in source && "bbox" in source;
}

function isGeoJSONSource(source: Layer["source"]): source is GeoJSONFeatureCollection {
  return typeof source === "object" && source !== null && "type" in source && (source as any).type === "FeatureCollection";
}

/** 大 ref 图层（_tileUrl 已配 + 要素数超阈值 + MVT 编码器可处理）→ MVT 矢量瓦片。
 * V3 Performance: use pre-computed descriptor when available, so the MVT
 * decision is made without requiring the full FC in the store. */
function isVectorTileLayer(layer: Layer): boolean {
  if (!layer._tileUrl) return false;
  // Fast path: use descriptor if present (avoids needing the full FC).
  // mvt_capable is false for raster/GeometryCollection-only/empty refs —
  // those must not be routed to the tile endpoint (it would render blank).
  if (layer._descriptor) {
    return (
      layer._descriptor.mvt_capable &&
      layer._descriptor.feature_count > VECTOR_TILE_THRESHOLD
    );
  }
  // Legacy path: requires FC already downloaded (pre-V3 refs without descriptor)
  return (
    isGeoJSONSource(layer.source) &&
    Array.isArray(layer.source.features) &&
    layer.source.features.length > VECTOR_TILE_THRESHOLD
  );
}

function parseDashArray(dash: string): number[] {
  switch (dash) {
    case "dashed": return [4, 2];
    case "dotted": return [1, 2];
    case "dashdot": return [4, 2, 1, 2];
    default: return [];
  }
}

// ---- FE-3: geometry-mix memo ----
//
// hudStateToMapSpec runs O(features) geometry scans per layer per reconcile
// (findings E2: the hasPolygons/hasLines/hasPoints scans + the weight scan).
// Layer sources are reused BY REFERENCE across reconciles — the worker-bridge's
// identity contract depends on that (FE-02), and the adapter emits
// `inlineData: layer.source` unchanged — so a WeakMap keyed on the
// FeatureCollection reference turns repeated scans into cache hits. A fresh FC
// object (new data) rescans exactly once; in-place mutation of a cached FC
// would be missed, which matches the worker-bridge's documented identity
// contract (sources are replaced, not mutated).

interface GeometryProfile {
  hasPolygons: boolean;
  hasLines: boolean;
  hasPoints: boolean;
  hasWeight: boolean;
}

const EMPTY_PROFILE: GeometryProfile = { hasPolygons: false, hasLines: false, hasPoints: false, hasWeight: false };

// Reassigned by _resetGeometryProfileCacheForTests (WeakMap has no clear()).
let geometryProfileCache: WeakMap<object, GeometryProfile> = new WeakMap();

/** Test-only: number of actual geometry scans performed since the last reset. */
export const _geometryProfileStats = { scanCount: 0 };

/** Test-only: drop the cache so tests start from a clean slate. */
export function _resetGeometryProfileCacheForTests(): void {
  geometryProfileCache = new WeakMap();
  _geometryProfileStats.scanCount = 0;
}

function geometryProfileOf(layer: Layer): GeometryProfile {
  // V3 Performance: use descriptor geometry_types when available — eliminates
  // 4× O(n) .some() scans on the full FC for MVT-backed large layers.
  if (layer._descriptor && layer._descriptor.geometry_types.length > 0) {
    const types = layer._descriptor.geometry_types;
    const profile: GeometryProfile = {
      hasPolygons: types.some((t) => t.includes("Polygon")),
      hasLines: types.some((t) => t.includes("Line")),
      hasPoints: types.some((t) => t.includes("Point")),
      hasWeight: false, // not tracked in descriptor; safe default
    };
    return profile;
  }
  const src = isGeoJSONSource(layer.source) ? layer.source : null;
  if (!src) return EMPTY_PROFILE;
  const cached = geometryProfileCache.get(src);
  if (cached) return cached;
  const features = src.features || [];
  const profile: GeometryProfile = {
    hasPolygons: features.some((f) => f.geometry?.type?.includes("Polygon")),
    hasLines: features.some((f) => f.geometry?.type?.includes("Line")),
    hasPoints: features.some((f) => f.geometry?.type?.includes("Point")),
    hasWeight: features.some((f) => (f as any).properties?.weight != null),
  };
  geometryProfileCache.set(src, profile);
  _geometryProfileStats.scanCount += 1;
  return profile;
}

// ---- the adapter ----

export function hudStateToMapSpec(input: HudToSpecInput): MapSpec {
  const { layers, processLayers, activeFilters, is3D } = input;
  const sources: Record<string, MapSpecSource> = {};
  const outLayers: MapSpecLayer[] = [];

  for (const layer of layers) {
    if (!layer.source) continue;

    // ---- 1. emit the source ----
    const sourceId = layer.id;
    if (layer.type === "raster" || layer.type === "tile") {
      // Tile URL source. Stored as a geojson-spec source carrying the url — the
      // runtime's patch-applier maps this to map.addSource({type:'raster',...}).
      sources[sourceId] = { type: "geojson", url: layer.source as string };
    } else if (isHeatmapRasterSource(layer.source)) {
      const src = layer.source;
      sources[sourceId] = { type: "raster", imageRef: src.image, bounds: src.bbox };
    } else if (isVectorTileLayer(layer)) {
      // Data Plane: 大 POI 图层用 MVT 矢量瓦片显示（否则整包 GeoJSON 下发）。
      sources[sourceId] = { type: "vector", tiles: [layer._tileUrl as string], minzoom: 1, maxzoom: 16 };
    } else if (isGeoJSONSource(layer.source)) {
      sources[sourceId] = { type: "geojson", inlineData: layer.source };
    } else {
      // Unknown source shape — emit empty geojson to keep the source present.
      sources[sourceId] = { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } as any };
    }

    // ---- 2. emit the layers ----
    const visibility: "visible" | "none" = layer.visible ? "visible" : "none";

    // Raster/tile layer (map-panel.tsx:231-239)
    if (layer.type === "raster" || layer.type === "tile") {
      const paint: Record<string, unknown> = { "raster-opacity": layer.opacity || 1 };
      if (layer.style?.brightness != null) paint["raster-brightness-max"] = layer.style.brightness;
      if (layer.style?.contrast != null) paint["raster-contrast"] = layer.style.contrast;
      if (layer.style?.saturation != null) paint["raster-saturation"] = layer.style.saturation;
      outLayers.push({
        id: `${layer.id}${SUBLAYER_SEP}main`,
        source: sourceId,
        type: "raster",
        paint: paint as any,
        layout: { visibility },
      });
      continue;
    }

    // Heatmap raster image source → raster layer (map-panel.tsx:240-244)
    if (layer.type === "heatmap" && isHeatmapRasterSource(layer.source)) {
      outLayers.push({
        id: `${layer.id}${SUBLAYER_SEP}raster`,
        source: sourceId,
        type: "raster",
        paint: { "raster-opacity": layer.opacity ?? 0.85, "raster-resampling": "linear" } as any,
        layout: { visibility },
      });
      continue;
    }

    // GeoJSON-source layers: introspect geometry mix (map-panel.tsx:246-253)
    const src = isGeoJSONSource(layer.source) ? layer.source : null;
    const { hasPolygons, hasLines, hasPoints, hasWeight } = geometryProfileOf(layer);
    const isNativeHeatmap = layer.type === "heatmap" && src && !isHeatmapRasterSource(layer.source);
    const isHeatmapMode = layer.type === "heatmap" || layer.style?.renderType === "heatmap" || layer.style?.renderType === "grid";

    // Filter expression builder (map-panel.tsx:207-214).
    // ADR-0052: when a layer carries a thematic legend_spec, its field is the
    // SINGLE identity shared by paint, legend filter and the legend UI — so the
    // range filter can never reference a different field than the painted one.
    // Falls back to source metadata.field for non-thematic layers (back compat).
    const srcMetaField = src && typeof src === "object" ? (src as any).metadata?.field : null;
    const filterField = thematicField(layer.legend_spec) ?? srcMetaField;
    const filterRanges = activeFilters[layer.id];
    const buildLayerFilter = (baseType: string): unknown[] => {
      const base: unknown[] = ["==", "$type", baseType];
      const parts: unknown[] = [base];
      // Issue #393: imperative filters (APPLY_LAYER_FILTER) are persisted on the
      // store layer — without this merge the reconcile would emit only the $type
      // base and silently roll the filter back. The imperative expression ANDs
      // with the geometry-type base so each sublayer keeps its own $type guard.
      if (layer.filter) parts.push(layer.filter);
      if (filterField && filterRanges) {
        const rangeFilters = filterRanges.map((range: number[]) => ["all", [">=", ["get", filterField], range[0]], ["<", ["get", filterField], range[1]]]);
        parts.push(["any", ...rangeFilters]);
      }
      return parts.length === 1 ? base : ["all", ...parts];
    };

    const color = layer.style?.color || "#16a34a";
    const strokeColor = layer.style?.strokeColor || layer.style?.color || "#16a34a";

    // ADR-0052: derive the live map's thematic color expression from the SAME
    // legend_spec the <ThematicLegend> overlay reads. Before this, the adapter
    // painted every feature as a flat `style.color`/`#16a34a` while the legend
    // showed a full classified palette — a maximal, undetectable drift. When no
    // thematic spec is present (plain single-color / apply_layer_style layers)
    // thematicColor is null and each color site keeps its legacy fill_color
    // coalesce + flat fallback, byte-identical to the pre-refactor behavior
    // (pinned by adapter.test.ts).
    const thematicColor = legendSpecToColorExpression(layer.legend_spec);

    const pushLayer = (sub: string, type: MapSpecLayer["type"], paint: MapSpecLayerPaint, filter?: unknown[]) => {
      outLayers.push({
        id: `${layer.id}${SUBLAYER_SEP}${sub}`,
        source: sourceId,
        type,
        paint,
        layout: { visibility },
        ...(filter ? { filter: filter as any } : {}),
      } as MapSpecLayer);
    };

    // Native MapLibre heatmap (map-panel.tsx:254-273)
    if (isNativeHeatmap) {
      pushLayer("native-heat", "heatmap", {
        "heatmap-weight": ["interpolate", ["linear"], ["get", "weight"], 0, 0, 1, 1] as any,
        "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 0, 1, 10, 3, 15, 5, 18, 8] as any,
        "heatmap-color": [
          "interpolate", ["linear"], ["heatmap-density"],
          0, "rgba(0,0,0,0)",
          0.1, "rgba(0,242,255,0.3)",
          0.3, "rgba(0,255,65,0.5)",
          0.5, "rgba(255,255,0,0.7)",
          0.7, "rgba(255,95,0,0.85)",
          1, "rgba(255,45,85,1)",
        ] as any,
        "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 0, 2, 5, 5, 9, 25, 12, 40, 15, 70, 18, 100] as any,
        "heatmap-opacity": ["interpolate", ["linear"], ["zoom"], 7, 1, 19, 0.85] as any,
      } as any);
    } else if (hasPolygons) {
      if (isHeatmapMode) {
        // Heatgrid fill (map-panel.tsx:275-293)
        pushLayer("heatgrid", "fill", {
          "fill-color": [
            "interpolate", ["linear"], ["get", "weight"],
            0.0, "rgba(0,0,0,0)",
            0.2, "rgba(0,242,255,0.4)",
            0.4, "rgba(0,255,65,0.6)",
            0.6, "rgba(255,255,0,0.7)",
            0.8, "rgba(255,95,0,0.85)",
            1.0, "rgba(255,45,85,0.95)",
          ] as any,
          "fill-outline-color": "rgba(255, 255, 255, 0.05)" as any,
          "fill-opacity": (layer.opacity ?? 1) as any,
          "fill-antialias": true as any,
        }, buildLayerFilter("Polygon"));
      } else {
        // Normal polygon: fill (map-panel.tsx:295-304)
        const fillEnabled = layer.style?.fill !== false;
        pushLayer("fill", "fill", {
          "fill-color": (fillEnabled
            ? (thematicColor ?? ["coalesce", ["get", "fill_color"], color])
            : "rgba(0,0,0,0)") as any,
          "fill-opacity": fillEnabled ? (layer.style?.fillOpacity ?? (layer.opacity || 1) * 0.3) : (0 as any),
        }, buildLayerFilter("Polygon"));

        // Conditional fill-extrusion when 3D (map-panel.tsx:306-317)
        if (is3D) {
          pushLayer("extrusion", "fill-extrusion", {
            "fill-extrusion-color": (thematicColor ?? color) as any,
            "fill-extrusion-height": ["coalesce", ["get", "height"], 20] as any,
            "fill-extrusion-base": (0 as any),
            "fill-extrusion-opacity": (layer.opacity || 0.8) as any,
          }, buildLayerFilter("Polygon"));
        }

        // Outline line (map-panel.tsx:318-328)
        const outlinePaint: Record<string, unknown> = {
          "line-color": (thematicColor ?? ["coalesce", ["get", "stroke_color"], ["get", "fill_color"], strokeColor]) as any,
          "line-width": layer.style?.strokeWidth ?? 2,
          "line-opacity": layer.opacity || 1,
        };
        if (layer.style?.dashArray && layer.style.dashArray !== "solid") {
          (outlinePaint as any)["line-dasharray"] = parseDashArray(layer.style.dashArray);
        }
        pushLayer("outline", "line", outlinePaint as any, buildLayerFilter("Polygon"));
      }
    }

    // Lines (map-panel.tsx:330-341)
    if (hasLines && !isNativeHeatmap) {
      const linePaint: Record<string, unknown> = {
        "line-color": (thematicColor ?? ["coalesce", ["get", "fill_color"], strokeColor]) as any,
        "line-width": layer.style?.strokeWidth ?? 2,
        "line-opacity": layer.opacity || 1,
      };
      if (layer.style?.dashArray && layer.style.dashArray !== "solid") {
        (linePaint as any)["line-dasharray"] = parseDashArray(layer.style.dashArray);
      }
      pushLayer("line", "line", linePaint as any, buildLayerFilter("LineString"));
    }

    // Points (map-panel.tsx:342-353)
    if (hasPoints && !isNativeHeatmap) {
      const radius = layer.style?.pointSize != null
        ? layer.style.pointSize
        : hasWeight
          ? ["interpolate", ["linear"], ["get", "weight"], 0, 4, 1, 8]
          : 6;
      pushLayer("point", "circle", {
        "circle-radius": radius as any,
        "circle-color": (thematicColor ?? ["coalesce", ["get", "fill_color"], color]) as any,
        "circle-stroke-width": (1.5 as any),
        "circle-stroke-color": "rgba(22, 163, 74, 0.3)" as any,
        "circle-opacity": (layer.opacity || 1) as any,
      }, buildLayerFilter("Point"));
    }
    // Note: map-panel.tsx:354-360 hides the stale point sublayer when a layer
    // no longer has points. Under the diff model this is automatic — the
    // absent `point` sublayer is reported as `remove` by diffSpecs.
  }

  // ---- process layers (map-panel.tsx:368-373, renderer.ts:449-494) ----
  for (const [stepId, geojson] of Object.entries(processLayers)) {
    const sourceId = `process-${stepId}`;
    sources[sourceId] = { type: "geojson", inlineData: geojson };
    outLayers.push({
      id: `${sourceId}${SUBLAYER_SEP}fill`,
      source: sourceId,
      type: "fill",
      paint: {
        "fill-color": "rgba(22, 163, 74, 0.08)",
        "fill-outline-color": "rgba(22, 163, 74, 0.3)",
      } as any,
    });
    outLayers.push({
      id: `${sourceId}${SUBLAYER_SEP}line`,
      source: sourceId,
      type: "line",
      paint: {
        "line-color": "#16a34a",
        "line-width": 1.5,
        "line-opacity": 0.4,
        "line-dasharray": [3, 3],
      } as any,
    });
    outLayers.push({
      id: `${sourceId}${SUBLAYER_SEP}point`,
      source: sourceId,
      type: "circle",
      filter: ["==", "$type", "Point"] as any,
      paint: {
        "circle-radius": 4,
        "circle-color": "rgba(22, 163, 74, 0.3)",
        "circle-stroke-width": 1,
        "circle-stroke-color": "#16a34a",
      } as any,
    });
  }

  return {
    version: "1.0",
    sources,
    layers: outLayers,
    // view is intentionally omitted — per ADR-0036 Q3 view stays imperative.
  };
}
